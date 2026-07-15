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

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "action": self.action,
            "reason": self.reason,
            "at": self.at.isoformat(),
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
            "created_at": self.created_at.isoformat(),
            "history": [transition.to_dict() for transition in self.history],
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
    ) -> None:
        self.audit_log = audit_log or InMemoryAuditLog()
        self.hooks: list[RealizationHook] = list(hooks or ())
        self.policy_version = policy_version
        self._store: DecisionStore = store or InMemoryDecisionStore()

    def register_hook(self, hook: RealizationHook) -> None:
        self.hooks.append(hook)

    def open_decision(
        self,
        report: SiteScoreReport,
        *,
        created_by: str,
        correlation_id: str = "",
    ) -> SiteScoreDecision:
        """Seed a decision from a SiteScore report (system recommendation)."""
        now = datetime.now(UTC)
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
            created_at=now,
        )
        self._store.save_report(decision.decision_id, report)
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
    ) -> SiteScoreDecision:
        decision = self._require(decision_id)
        if decision.status not in {DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.DRAFT}:
            raise SiteScoreDecisionError(
                f"cannot submit decision in status {decision.status.value} for review"
            )
        updated = self._transition(
            decision,
            to_status=DecisionStatus.PENDING_REVIEW,
            actor=submitted_by,
            action="submit",
            reason="送審",
        )
        self._record_audit(
            updated,
            action="submit",
            outcome="pending_review",
            actor=submitted_by,
            correlation_id=correlation_id,
            reason="送審",
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
        updated = self._transition(
            decision,
            to_status=target,
            actor=actor,
            action=resolved_action.value,
            reason=reason,
        )

        realization_events: tuple[SiteScoreRealizationEvent, ...] = ()
        if target is DecisionStatus.APPROVED:
            realization_events = self._realize(updated, actor=actor)

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
    ) -> SiteScoreDecision:
        transition = DecisionTransition(
            from_status=decision.status,
            to_status=to_status,
            actor=actor,
            action=action,
            reason=reason,
            at=datetime.now(UTC),
        )
        updated = SiteScoreDecision(
            **{
                **decision.__dict__,
                "status": to_status,
                "history": (*decision.history, transition),
            }
        )
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
                },
            )
        )

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
