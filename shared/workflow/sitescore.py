"""SiteScore decision workflow and realization hooks.

Implements the expansion decision closed loop (ODP nav/workflow §8.1):

    SiteScore Report (system recommendation)
      → 送審 submit for review            → PENDING_REVIEW
      → 核准 / 退回 / 補件 human decision   → APPROVED / REJECTED / DRAFT
      → realization hooks + Decision Audit

Approval is high risk: it requires an explicit reason, is never optimistic, and
emits an audit event. On approval the workflow fires registered realization
hooks so downstream consumers (candidate-site status, forecast baseline
registration for sitescore_gap_ratio tracking) can realize the decision.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from modules.sitescore.domain.scoring import SiteScoreRecommendation, SiteScoreReport
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.domain.models import Approval

POLICY_VERSION = "sitescore-decision-policy-v1"


class DecisionStatus(StrEnum):
    DRAFT = "DRAFT"
    SYSTEM_RECOMMENDED = "SYSTEM_RECOMMENDED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"


# Actions that commit a high-risk human decision and therefore require a reason.
_REASON_REQUIRED = {DecisionAction.APPROVE, DecisionAction.REJECT}

_ACTION_TARGET = {
    DecisionAction.APPROVE: DecisionStatus.APPROVED,
    DecisionAction.REJECT: DecisionStatus.REJECTED,
    DecisionAction.REQUEST_REVISION: DecisionStatus.DRAFT,
}


class SiteScoreDecisionError(ValueError):
    """Raised on an invalid decision transition or a missing required reason."""


@dataclass(frozen=True)
class DecisionTransition:
    from_status: DecisionStatus
    to_status: DecisionStatus
    actor: str
    action: str
    reason: str
    at: datetime
    network_action: str | None = None
    conditions: str | None = None
    required_data: tuple[str, ...] | None = None
    override_ack: bool | None = None
    actor_role_id: str | None = None
    tenant_id: str | None = None
    report_id: str | None = None
    report_version: int | None = None
    model_version: str | None = None
    approval_id: str | None = None
    submitted_by: str | None = None
    submission_actor_verified: bool = False
    submission_summary: str | None = None
    reviewer_group_id: str | None = None
    previous_decision_id: str | None = None
    version: int | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "action": self.action,
            "reason": self.reason,
            "at": self.at.isoformat(),
            **{
                key: getattr(self, key, None)
                for key in ("network_action", "conditions", "override_ack", "actor_role_id",
                            "tenant_id", "report_id", "report_version", "model_version", "approval_id", "version", "correlation_id",
                            "submitted_by", "submission_actor_verified", "submission_summary", "reviewer_group_id")
            },
            "required_data": (list(self.required_data) if getattr(self, "required_data", None) is not None else None),
            **({"previous_decision_id": self.previous_decision_id}
               if getattr(self, "previous_decision_id", None) is not None else {}),
        }


@dataclass(frozen=True)
class SiteScoreDecision:
    decision_id: str
    candidate_site_id: str
    report_id: str
    report_version: int
    recommendation: SiteScoreRecommendation
    status: DecisionStatus
    policy_version: str
    model_version: str
    created_by: str
    created_at: datetime
    history: tuple[DecisionTransition, ...] = ()
    tenant_id: str = ""
    created_by_verified: bool = False
    # Zero denotes a legacy record without a persisted concurrency version.
    version: int = 0
    network_action: str | None = None
    conditions: str | None = None
    required_data: tuple[str, ...] | None = None
    override_ack: bool | None = None
    actor_role_id: str | None = None
    approval_id: str | None = None
    submitted_by: str | None = None
    submission_actor_verified: bool = False
    submission_summary: str | None = None
    reviewer_group_id: str | None = None
    # A later report is reviewed separately after an existing approval. The
    # predecessor and its opening realization remain the original record.
    previous_decision_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {DecisionStatus.APPROVED, DecisionStatus.REJECTED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_site_id": self.candidate_site_id,
            "report_id": self.report_id,
            "report_version": self.report_version,
            "recommendation": self.recommendation.value,
            "decision_status": self.status.value,
            "policy_version": self.policy_version,
            "model_version": self.model_version,
            "created_by": self.created_by,
            "created_by_verified": getattr(self, "created_by_verified", False),
            "created_at": self.created_at.isoformat(),
            "history": [transition.to_dict() for transition in self.history],
            "tenant_id": self.tenant_id,
            "version": getattr(self, "version", 0),
            **({"previous_decision_id": self.previous_decision_id}
               if getattr(self, "previous_decision_id", None) is not None else {}),
            **{key: getattr(self, key, None) for key in
               ("network_action", "conditions", "override_ack", "actor_role_id", "approval_id",
                "submitted_by", "submission_actor_verified", "submission_summary", "reviewer_group_id")},
            "required_data": (list(self.required_data) if getattr(self, "required_data", None) is not None else None),
        }


@dataclass(frozen=True)
class SiteScoreRealizationEvent:
    """Emitted when an approved decision is realized into the pipeline."""

    decision_id: str
    candidate_site_id: str
    report_id: str
    report_version: int
    recommendation: SiteScoreRecommendation
    baseline_trajectory: dict[str, float]
    payback_p50_months: float
    # Inputs frozen at the moment of approval (acceptance: input snapshot /
    # model_version / policy_version are immutable once a decision is approved).
    model_version: str
    policy_version: str
    input_snapshot_ids: tuple[str, ...]
    feature_snapshot_time: datetime
    actor: str
    realized_at: datetime
    target_site_status: str = "approved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_site_id": self.candidate_site_id,
            "report_id": self.report_id,
            "report_version": self.report_version,
            "recommendation": self.recommendation.value,
            "baseline_trajectory": dict(self.baseline_trajectory),
            "payback_p50_months": self.payback_p50_months,
            "model_version": self.model_version,
            "policy_version": self.policy_version,
            "input_snapshot_ids": list(self.input_snapshot_ids),
            "feature_snapshot_time": self.feature_snapshot_time.isoformat(),
            "actor": self.actor,
            "realized_at": self.realized_at.isoformat(),
            "target_site_status": self.target_site_status,
        }


class RealizationHook(Protocol):
    def __call__(self, event: SiteScoreRealizationEvent) -> None:
        ...


@dataclass(frozen=True)
class RealizedSite:
    candidate_site_id: str
    decision_id: str
    site_status: str
    baseline_trajectory: dict[str, float]
    realized_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_site_id": self.candidate_site_id,
            "decision_id": self.decision_id,
            "site_status": self.site_status,
            "baseline_trajectory": dict(self.baseline_trajectory),
            "realized_at": self.realized_at.isoformat(),
        }


class RealizedSiteStore(Protocol):
    """Storage surface for realized candidate sites.

    A durable implementation (SQLite document store) lets the realization hook
    survive a process restart; the in-memory default keeps the previous
    behaviour for unit tests and fast local boot.
    """

    def put(self, site: RealizedSite) -> None:
        ...

    def get(self, candidate_site_id: str) -> RealizedSite | None:
        ...

    def list_realized(self) -> list[RealizedSite]:
        ...


class InMemoryRealizedSiteStore:
    """Default in-memory :class:`RealizedSiteStore` keyed by candidate site."""

    def __init__(self) -> None:
        self._realized: dict[str, RealizedSite] = {}

    def put(self, site: RealizedSite) -> None:
        self._realized[site.candidate_site_id] = site

    def get(self, candidate_site_id: str) -> RealizedSite | None:
        return self._realized.get(candidate_site_id)

    def list_realized(self) -> list[RealizedSite]:
        return list(self._realized.values())


class CandidateSiteRealizationHook:
    """Default hook: marks realized candidate sites and stores their forecast
    baseline so downstream forecast realization (``sitescore_gap_ratio``) can be
    computed against the approved SiteScore report.

    The realized sites are held in a pluggable :class:`RealizedSiteStore`; inject
    a durable store to make ``/sitescore/realized`` survive a restart."""

    def __init__(self, store: RealizedSiteStore | None = None) -> None:
        self._store: RealizedSiteStore = store or InMemoryRealizedSiteStore()

    def __call__(self, event: SiteScoreRealizationEvent) -> None:
        self._store.put(
            RealizedSite(
                candidate_site_id=event.candidate_site_id,
                decision_id=event.decision_id,
                site_status=event.target_site_status,
                baseline_trajectory=dict(event.baseline_trajectory),
                realized_at=event.realized_at,
            )
        )

    def get(self, candidate_site_id: str) -> RealizedSite | None:
        return self._store.get(candidate_site_id)

    def list_realized(self) -> list[RealizedSite]:
        return self._store.list_realized()


@dataclass(frozen=True)
class SiteScoreDecisionOutcome:
    decision: SiteScoreDecision
    audit_event_id: str
    realization_events: tuple[SiteScoreRealizationEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision.decision_id,
            "decision_status": self.decision.status.value,
            "audit_event_id": self.audit_event_id,
            "realization_events": [event.to_dict() for event in self.realization_events],
            "decision": self.decision.to_dict(),
        }


class DecisionStore(Protocol):
    """Storage surface for open decisions and their frozen source reports.

    The report a decision was opened against is retained so an approval can be
    realized against the exact inputs a human reviewed, even after a restart.
    """

    def save_decision(self, decision: SiteScoreDecision) -> None:
        ...

    def save_report(self, decision_id: str, report: SiteScoreReport) -> None:
        ...

    def get_decision(self, decision_id: str) -> SiteScoreDecision | None:
        ...

    def get_report(self, decision_id: str) -> SiteScoreReport | None:
        ...

    def list_decisions(self) -> list[SiteScoreDecision]:
        ...


class InMemoryDecisionStore:
    """Default in-memory :class:`DecisionStore`."""

    def __init__(self) -> None:
        self._decisions: dict[str, SiteScoreDecision] = {}
        self._reports: dict[str, SiteScoreReport] = {}

    def save_decision(self, decision: SiteScoreDecision) -> None:
        self._decisions[decision.decision_id] = decision

    def save_report(self, decision_id: str, report: SiteScoreReport) -> None:
        self._reports[decision_id] = report

    def get_decision(self, decision_id: str) -> SiteScoreDecision | None:
        return self._decisions.get(decision_id)

    def get_report(self, decision_id: str) -> SiteScoreReport | None:
        return self._reports.get(decision_id)

    def list_decisions(self) -> list[SiteScoreDecision]:
        return list(self._decisions.values())


class SiteScoreDecisionWorkflow:
    """State machine for the SiteScore human-approval closed loop."""

    def __init__(
        self,
        *,
        audit_log: InMemoryAuditLog | None = None,
        hooks: Iterable[RealizationHook] | None = None,
        policy_version: str = POLICY_VERSION,
        store: DecisionStore | None = None,
        approval_store: Any | None = None,
    ) -> None:
        self.audit_log = audit_log or InMemoryAuditLog()
        self.hooks: list[RealizationHook] = list(hooks or ())
        self.policy_version = policy_version
        self._store: DecisionStore = store or InMemoryDecisionStore()
        self._approval_store = approval_store

    def register_hook(self, hook: RealizationHook) -> None:
        self.hooks.append(hook)

    def _validate_previous_round(self, report: SiteScoreReport, previous_decision_id: str,
                                 tenant_id: str) -> None:
        """Require a retained, ordered chain rooted in an original approval.

        A later round may be rejected without revoking the original opening.
        An initial rejection has no approved predecessor and cannot use this
        policy to reopen. This method reads records and performs no transition.
        """
        seen: set[str] = set()
        next_report = report
        while previous_decision_id is not None:
            if (not isinstance(previous_decision_id, str) or not previous_decision_id.strip()
                    or previous_decision_id in seen):
                raise SiteScoreDecisionError("invalid previous review round chain")
            seen.add(previous_decision_id)
            previous = self._require(previous_decision_id)
            previous_link = getattr(previous, "previous_decision_id", None)
            if any(getattr(item, "previous_decision_id", None) != previous_link for item in previous.history):
                raise SiteScoreDecisionError("previous review round changed after its recorded transitions")
            retained = self._store.get_report(previous.decision_id)
            approval = (self._approval_store.get(previous.approval_id)
                        if self._approval_store is not None and previous.approval_id else None)
            if (previous.decision_id != previous_decision_id
                    or previous.status not in {DecisionStatus.APPROVED, DecisionStatus.REJECTED}
                    or previous.candidate_site_id != report.candidate_site_id
                    or previous.tenant_id != tenant_id
                    or type(previous.version) is not int or previous.version < 1
                    or type(previous.report_version) is not int or previous.report_version < 1
                    or type(next_report.report_version) is not int
                    or next_report.report_version <= previous.report_version
                    or next_report.report_id == previous.report_id
                    or not isinstance(previous.report_id, str) or not previous.report_id.strip()
                    or not isinstance(previous.model_version, str) or not previous.model_version.strip()
                    or not isinstance(retained, SiteScoreReport)
                    or type(retained.report_version) is not int
                    or (retained.candidate_site_id, retained.report_id, retained.report_version, retained.model_version)
                    != (report.candidate_site_id, previous.report_id, previous.report_version, previous.model_version)):
                raise SiteScoreDecisionError("new review round requires a retained older decision for this candidate and tenant")
            if (not isinstance(approval, Approval) or approval.approval_id != previous.approval_id
                    or approval.decision_id != previous.decision_id
                    or approval.approval_status != ("approved" if previous.status is DecisionStatus.APPROVED else "rejected")
                    or not previous.history or not isinstance(approval.approver_id, str) or not approval.approver_id.strip()
                    or approval.approver_id != previous.history[-1].actor
                    or not isinstance(approval.approved_at, datetime)
                    or not isinstance(previous.history[-1].at, datetime)
                    or approval.approved_at != previous.history[-1].at
                    or approval.comment != previous.history[-1].reason):
                raise SiteScoreDecisionError("previous review round requires its actual retained terminal approval")
            terminal = previous.history[-1]
            allowed_network_actions = ((None, "GO", "WAIT") if previous.status is DecisionStatus.APPROVED
                                       else (None, "REJECT"))
            if (previous.network_action not in allowed_network_actions
                    or previous.network_action == "WAIT" and (
                        not isinstance(previous.conditions, str) or not previous.conditions.strip())
                    or terminal.from_status is not DecisionStatus.PENDING_REVIEW
                    or type(terminal.version) is not int or type(terminal.report_version) is not int
                    or terminal.action != ("APPROVE" if previous.status is DecisionStatus.APPROVED else "REJECT")
                    or (terminal.to_status, terminal.report_id, terminal.report_version, terminal.model_version,
                        terminal.tenant_id, terminal.version, terminal.approval_id, terminal.network_action,
                        terminal.conditions, terminal.required_data, terminal.override_ack, terminal.actor_role_id)
                    != (previous.status, previous.report_id, previous.report_version, previous.model_version,
                        previous.tenant_id, previous.version, previous.approval_id, previous.network_action,
                        previous.conditions, previous.required_data, previous.override_ack, previous.actor_role_id)):
                raise SiteScoreDecisionError("previous terminal transition must match its actual decision and retained report")
            previous_decision_id = getattr(previous, "previous_decision_id", None)
            if previous_decision_id is None and previous.status is not DecisionStatus.APPROVED:
                raise SiteScoreDecisionError("new review round must retain an original approved decision")
            next_report = retained

    def _validate_existing_round(self, decision: SiteScoreDecision) -> SiteScoreReport:
        previous_id = getattr(decision, "previous_decision_id", None)
        if any(getattr(item, "previous_decision_id", None) != previous_id for item in decision.history):
            raise SiteScoreDecisionError("previous review round changed after submission")
        retained = self._store.get_report(decision.decision_id)
        if (type(decision.version) is not int or decision.version < 1
                or type(decision.report_version) is not int or decision.report_version < 1
                or not isinstance(retained, SiteScoreReport) or type(retained.report_version) is not int
                or (retained.candidate_site_id, retained.report_id, retained.report_version, retained.model_version)
                != (decision.candidate_site_id, decision.report_id, decision.report_version, decision.model_version)):
            raise SiteScoreDecisionError("new review round requires its immutable current retained report")
        if previous_id is None:
            return retained
        if decision.status is DecisionStatus.PENDING_REVIEW:
            tail = decision.history[-1] if decision.history else None
            approval = (self._approval_store.get(decision.approval_id)
                        if self._approval_store is not None and decision.approval_id else None)
            if (tail is None or tail.from_status not in {DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.DRAFT}
                    or tail.to_status is not decision.status or tail.action != "submit"
                    or type(tail.version) is not int or type(tail.report_version) is not int
                    or (tail.version, tail.report_id, tail.report_version, tail.model_version, tail.tenant_id,
                        tail.approval_id, tail.previous_decision_id)
                    != (decision.version, decision.report_id, decision.report_version, decision.model_version,
                        decision.tenant_id, decision.approval_id, previous_id)
                    or not isinstance(tail.at, datetime)
                    or not isinstance(decision.submitted_by, str) or not decision.submitted_by.strip()
                    or tail.actor != decision.submitted_by or tail.submitted_by != decision.submitted_by
                    or not isinstance(decision.submission_actor_verified, bool)
                    or tail.submission_actor_verified is not decision.submission_actor_verified
                    or tail.reason != (decision.submission_summary if decision.submission_summary is not None else "送審")
                    or tail.submission_summary != decision.submission_summary
                    or tail.reviewer_group_id != decision.reviewer_group_id
                    or not isinstance(approval, Approval) or approval.approval_id != decision.approval_id
                    or approval.decision_id != decision.decision_id or approval.approval_status != "pending"):
                raise SiteScoreDecisionError("new pending review requires its actual current submit transition and approval")
        self._validate_previous_round(retained, previous_id, decision.tenant_id)
        return retained

    def open_decision(
        self,
        report: SiteScoreReport,
        *,
        created_by: str,
        correlation_id: str = "",
        tenant_id: str = "",
        verified_creator: bool = False,
        previous_decision_id: str | None = None,
    ) -> SiteScoreDecision:
        """Seed a decision from a SiteScore report (system recommendation)."""
        now = datetime.now(UTC)
        store_tenant = getattr(self._store, "tenant_id", "")
        if store_tenant and tenant_id and store_tenant != tenant_id:
            raise SiteScoreDecisionError("decision tenant differs from the scoped repository")
        tenant_id = tenant_id or store_tenant
        if previous_decision_id is not None:
            self._validate_previous_round(report, previous_decision_id, tenant_id)
        decision = SiteScoreDecision(
            decision_id=f"sitescore-decision-{uuid4()}",
            candidate_site_id=report.candidate_site_id,
            report_id=report.report_id,
            report_version=report.report_version,
            recommendation=report.recommendation,
            status=DecisionStatus.SYSTEM_RECOMMENDED,
            policy_version=self.policy_version,
            model_version=report.model_version,
            created_by=created_by,
            created_by_verified=verified_creator,
            created_at=now,
            tenant_id=tenant_id,
            version=1,
            previous_decision_id=previous_decision_id,
        )
        retain = getattr(self._store, "retain_report_once", self._store.save_report)
        retain(decision.decision_id, report)
        self._store.save_decision(decision)
        self._record_audit(
            decision,
            action="create",
            outcome="system_recommended",
            actor=created_by,
            correlation_id=correlation_id,
            reason=f"system recommendation {report.recommendation.value}",
        )
        return decision

    def submit_for_review(
        self,
        decision_id: str,
        *,
        submitted_by: str,
        correlation_id: str = "",
        submission_summary: str | None = None,
        reviewer_group_id: str | None = None,
        verified_submitter: bool = False,
    ) -> SiteScoreDecision:
        decision = self._require(decision_id)
        if decision.status not in {DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.DRAFT}:
            raise SiteScoreDecisionError(
                f"cannot submit decision in status {decision.status.value} for review"
            )
        self._validate_existing_round(decision)
        updated = self._transition(
            decision,
            to_status=DecisionStatus.PENDING_REVIEW,
            actor=submitted_by,
            action="submit",
            reason=submission_summary if submission_summary is not None else "送審",
            submitted_by=submitted_by,
            submission_actor_verified=verified_submitter,
            submission_summary=submission_summary,
            reviewer_group_id=reviewer_group_id,
            approval_id=self._create_approval(decision),
            correlation_id=correlation_id,
        )
        self._record_audit(
            updated,
            action="submit",
            outcome="pending_review",
            actor=submitted_by,
            correlation_id=correlation_id,
            reason=submission_summary if submission_summary is not None else "送審",
        )
        return updated

    def decide(
        self,
        decision_id: str,
        *,
        action: DecisionAction | str,
        actor: str,
        reason: str = "",
        correlation_id: str = "",
        network_action: str | None = None,
        conditions: str | None = None,
        required_data: Iterable[str] | None = None,
        override_ack: bool | None = None,
        actor_role_id: str | None = None,
    ) -> SiteScoreDecisionOutcome:
        decision = self._require(decision_id)
        resolved_action = DecisionAction(action)
        if decision.status is not DecisionStatus.PENDING_REVIEW:
            raise SiteScoreDecisionError(
                f"cannot decide on decision in status {decision.status.value}; must be PENDING_REVIEW"
            )
        if resolved_action in _REASON_REQUIRED and not reason.strip():
            raise SiteScoreDecisionError(
                f"{resolved_action.value} is a high-risk action and requires a reason"
            )

        target = _ACTION_TARGET[resolved_action]
        # Existing native APPROVE/REJECT calls retain their documented behavior.
        # Old persisted records without this evidence remain unknown.
        explicit_network_action = network_action
        recorded_required_data = tuple(required_data) if required_data is not None else None
        if explicit_network_action is None:
            explicit_network_action = {
                DecisionAction.APPROVE: "GO", DecisionAction.REJECT: "REJECT",
            }.get(resolved_action)
        if explicit_network_action is not None:
            expected = {"GO": DecisionAction.APPROVE, "WAIT": DecisionAction.APPROVE,
                        "RETURN": DecisionAction.REQUEST_REVISION, "REJECT": DecisionAction.REJECT}
            if expected.get(explicit_network_action) is not resolved_action:
                raise SiteScoreDecisionError("network action does not match the native transition")
            if explicit_network_action == "WAIT" and (not isinstance(conditions, str) or not conditions.strip()):
                raise SiteScoreDecisionError("WAIT requires its recorded approval conditions")
            if explicit_network_action == "RETURN" and (not recorded_required_data or not all(
                isinstance(item, str) and item.strip() for item in recorded_required_data
            )):
                raise SiteScoreDecisionError("RETURN requires its recorded missing-data list")
        report = self._validate_existing_round(decision)
        if explicit_network_action == "GO" and not report.has_financial_result:
            raise SiteScoreDecisionError("GO requires the retained report's complete financial inputs and payback")
        updated = self._transition(
            decision,
            to_status=target,
            actor=actor,
            action=resolved_action.value,
            reason=reason,
            network_action=explicit_network_action,
            conditions=conditions,
            required_data=recorded_required_data,
            override_ack=override_ack,
            actor_role_id=actor_role_id,
            approval_id=(getattr(decision, "approval_id", None) or self._create_approval(decision)),
            correlation_id=correlation_id,
        )

        realization_events: tuple[SiteScoreRealizationEvent, ...] = ()
        if (target is DecisionStatus.APPROVED and explicit_network_action != "WAIT"
                and getattr(updated, "previous_decision_id", None) is None):
            realization_events = self._realize(updated, actor=actor)

        self._decide_approval(updated, actor=actor, reason=reason)

        audit_event = self._record_audit(
            updated,
            action=_AUDIT_ACTION[resolved_action],
            outcome=target.value.lower(),
            actor=actor,
            correlation_id=correlation_id,
            reason=reason,
            realized=len(realization_events),
        )
        return SiteScoreDecisionOutcome(
            decision=updated,
            audit_event_id=audit_event.event_id,
            realization_events=realization_events,
        )

    def get(self, decision_id: str) -> SiteScoreDecision | None:
        return self._store.get_decision(decision_id)

    def list_decisions(self) -> list[SiteScoreDecision]:
        return self._store.list_decisions()

    def _realize(
        self,
        decision: SiteScoreDecision,
        *,
        actor: str,
    ) -> tuple[SiteScoreRealizationEvent, ...]:
        if getattr(decision, "network_action", None) == "WAIT":
            return ()
        report = self._store.get_report(decision.decision_id)
        if report is None:
            raise SiteScoreDecisionError(
                f"no source report retained for decision {decision.decision_id}"
            )
        event = SiteScoreRealizationEvent(
            decision_id=decision.decision_id,
            candidate_site_id=decision.candidate_site_id,
            report_id=report.report_id,
            report_version=report.report_version,
            recommendation=report.recommendation,
            baseline_trajectory=report.baseline_trajectory(),
            payback_p50_months=report.payback_p50_months,
            model_version=report.model_version,
            policy_version=decision.policy_version,
            input_snapshot_ids=report.source_snapshot_ids,
            feature_snapshot_time=report.feature_snapshot_time,
            actor=actor,
            realized_at=datetime.now(UTC),
        )
        for hook in self.hooks:
            hook(event)
        return (event,)

    def _transition(
        self,
        decision: SiteScoreDecision,
        *,
        to_status: DecisionStatus,
        actor: str,
        action: str,
        reason: str,
        network_action: str | None = None,
        conditions: str | None = None,
        required_data: tuple[str, ...] | None = None,
        override_ack: bool | None = None,
        actor_role_id: str | None = None,
        approval_id: str | None = None,
        correlation_id: str = "",
        submitted_by: str | None = None,
        submission_actor_verified: bool | None = None,
        submission_summary: str | None = None,
        reviewer_group_id: str | None = None,
    ) -> SiteScoreDecision:
        submission = {
            "submitted_by": submitted_by if submitted_by is not None else getattr(decision, "submitted_by", None),
            "submission_actor_verified": submission_actor_verified if submission_actor_verified is not None else getattr(decision, "submission_actor_verified", False),
            "submission_summary": submission_summary if submission_summary is not None else getattr(decision, "submission_summary", None),
            "reviewer_group_id": reviewer_group_id if reviewer_group_id is not None else getattr(decision, "reviewer_group_id", None),
        }
        transition = DecisionTransition(
            **submission,
            from_status=decision.status,
            to_status=to_status,
            actor=actor,
            action=action,
            reason=reason,
            at=datetime.now(UTC),
            network_action=network_action,
            conditions=conditions,
            required_data=required_data,
            override_ack=override_ack,
            actor_role_id=actor_role_id,
            tenant_id=decision.tenant_id,
            report_id=decision.report_id,
            report_version=decision.report_version,
            model_version=decision.model_version,
            approval_id=approval_id or getattr(decision, "approval_id", None),
            version=getattr(decision, "version", 0) + 1,
            correlation_id=correlation_id,
            previous_decision_id=getattr(decision, "previous_decision_id", None),
        )
        updated = SiteScoreDecision(
            **{
                **decision.__dict__,
                **submission,
                "status": to_status,
                "history": (*decision.history, transition),
                "version": getattr(decision, "version", 0) + 1,
                "network_action": network_action,
                "conditions": conditions,
                "required_data": required_data,
                "override_ack": override_ack,
                "actor_role_id": actor_role_id,
                "approval_id": approval_id or getattr(decision, "approval_id", None),
            }
        )
        cas = getattr(self._store, "save_decision_version", None)
        if cas is not None:
            cas(updated, expected_version=getattr(decision, "version", 0))
        else:
            self._store.save_decision(updated)
        return updated

    def _record_audit(
        self,
        decision: SiteScoreDecision,
        *,
        action: str,
        outcome: str,
        actor: str,
        correlation_id: str,
        reason: str,
        realized: int = 0,
    ) -> AuditEvent:
        return self.audit_log.record(
            AuditEvent(
                event_type="sitescore.decision.v1",
                actor=actor,
                action=action,
                resource=f"sitescore/decision/{decision.decision_id}",
                outcome=outcome,
                correlation_id=correlation_id,
                metadata={
                    "candidate_site_id": decision.candidate_site_id,
                    "report_id": decision.report_id,
                    "report_version": decision.report_version,
                    "recommendation": decision.recommendation.value,
                    "decision_status": decision.status.value,
                    "policy_version": decision.policy_version,
                    "reason": reason,
                    "realized_sites": realized,
                    "tenant_id": decision.tenant_id,
                    "model_version": decision.model_version,
                    "version": getattr(decision, "version", 0),
                    **({"previous_decision_id": decision.previous_decision_id}
                       if getattr(decision, "previous_decision_id", None) is not None else {}),
                    **{key: getattr(decision, key, None) for key in
                       ("network_action", "conditions", "override_ack", "actor_role_id", "approval_id",
                        "submitted_by", "submission_actor_verified", "submission_summary", "reviewer_group_id")},
                    "required_data": (list(decision.required_data) if getattr(decision, "required_data", None) is not None else None),
                },
            )
        )

    def _create_approval(self, decision: SiteScoreDecision) -> str | None:
        if self._approval_store is None:
            return None
        approval = Approval(decision_id=decision.decision_id)
        self._approval_store.save(approval)
        return approval.approval_id

    def _decide_approval(self, decision: SiteScoreDecision, *, actor: str, reason: str) -> None:
        if self._approval_store is None:
            return
        approval_id = getattr(decision, "approval_id", None)
        approval = self._approval_store.get(approval_id) if approval_id else None
        if not isinstance(approval, Approval) or approval.decision_id != decision.decision_id:
            raise SiteScoreDecisionError("no associated canonical approval retained for decision")
        status = {DecisionStatus.APPROVED: "approved", DecisionStatus.REJECTED: "rejected",
                  DecisionStatus.DRAFT: "returned"}[decision.status]
        self._approval_store.save(Approval(approval_id=approval.approval_id,
                                         decision_id=decision.decision_id, approver_id=actor,
                                         approval_status=status, approved_at=decision.history[-1].at,
                                         comment=reason))

    def _require(self, decision_id: str) -> SiteScoreDecision:
        decision = self._store.get_decision(decision_id)
        if decision is None:
            raise SiteScoreDecisionError(f"unknown decision {decision_id}")
        return decision


_AUDIT_ACTION = {
    DecisionAction.APPROVE: "approve",
    DecisionAction.REJECT: "reject",
    DecisionAction.REQUEST_REVISION: "return",
}


__all__ = [
    "POLICY_VERSION",
    "CandidateSiteRealizationHook",
    "DecisionAction",
    "DecisionStore",
    "DecisionStatus",
    "DecisionTransition",
    "InMemoryDecisionStore",
    "InMemoryRealizedSiteStore",
    "RealizationHook",
    "RealizedSite",
    "RealizedSiteStore",
    "SiteScoreDecision",
    "SiteScoreDecisionError",
    "SiteScoreDecisionOutcome",
    "SiteScoreDecisionWorkflow",
    "SiteScoreRealizationEvent",
]
