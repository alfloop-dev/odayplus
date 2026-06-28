"""InterventionOps lifecycle domain model.

Implements the shared operational-intervention lifecycle (ODP-MOD-05) reused by
PriceOps, AdLift, promotion, CRM recall, maintenance and cleaning. The module is
deliberately treatment-agnostic: a single state machine, conflict policy,
observation window and evidence model serve every treatment type.

Canonical state machine (ODP-MOD-05 §7):

    CANDIDATE
      → ELIGIBILITY_CHECKING → ELIGIBLE | INELIGIBLE
      → ACTION_PROPOSED
      → CONFLICT_CHECKING            (overlap / contamination control, §4 ODP-FR-INTV-004)
      → PENDING_APPROVAL → APPROVED | REJECTED   (human approval, separated from execution)
      → EXECUTING → OBSERVING        (observation window opens at execution)
      → EVALUATING → COMPLETED | STOPPED | ROLLED_BACK

Causal guardrails (ODP-ML-05 §2, §5, §6):

- Before/after is not causality. Effect can only be claimed once the observation
  window has matured (transactions / refunds / cost settled).
- A causal claim additionally requires a matched control group with a passing
  pre-trend check; otherwise the report stays descriptive.
- Evidence Level (L0–L5) is always attached so the UI never overstates certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

POLICY_VERSION = "intervention-lifecycle-policy-v1"
FEATURE_VERSION = "intervention-feature-v1"
MEASUREMENT_METHOD_DEFAULT = "panel_did"


class InterventionKind(StrEnum):
    """Treatment taxonomy (ODP-ML-05 §3)."""

    PRICE_CHANGE = "PRICE_CHANGE"
    AD_CAMPAIGN = "AD_CAMPAIGN"
    PROMOTION = "PROMOTION"
    CRM_RECALL = "CRM_RECALL"
    MAINTENANCE = "MAINTENANCE"
    CLEANING = "CLEANING"
    OPENING_CAMPAIGN = "OPENING_CAMPAIGN"
    EXTERNAL_SHOCK = "EXTERNAL_SHOCK"


class InterventionStatus(StrEnum):
    """Lifecycle states (ODP-MOD-05 §7)."""

    CANDIDATE = "CANDIDATE"
    ELIGIBILITY_CHECKING = "ELIGIBILITY_CHECKING"
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    CONFLICT_CHECKING = "CONFLICT_CHECKING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ROLLED_BACK = "ROLLED_BACK"


# Terminal states never transition further.
TERMINAL_STATUSES = frozenset(
    {
        InterventionStatus.INELIGIBLE,
        InterventionStatus.REJECTED,
        InterventionStatus.COMPLETED,
        InterventionStatus.STOPPED,
        InterventionStatus.ROLLED_BACK,
    }
)

# Statuses whose planned window still competes for a store's timeline, so a new
# intervention overlapping them is a contamination risk (ODP-ML-05 §7, §8.3).
ACTIVE_CONFLICT_STATUSES = frozenset(
    {
        InterventionStatus.ACTION_PROPOSED,
        InterventionStatus.CONFLICT_CHECKING,
        InterventionStatus.PENDING_APPROVAL,
        InterventionStatus.APPROVED,
        InterventionStatus.EXECUTING,
        InterventionStatus.OBSERVING,
        InterventionStatus.EVALUATING,
    }
)


class PretrendStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvaluationMethod(StrEnum):
    NONE = "NONE"
    BEFORE_AFTER = "BEFORE_AFTER"
    MATCHED_BEFORE_AFTER = "MATCHED_BEFORE_AFTER"
    DID = "DID"
    SYNTHETIC_CONTROL = "SYNTHETIC_CONTROL"
    RCT = "RCT"


class EvidenceLevel(StrEnum):
    """Evidence Level ladder (ODP-ML-05 §5)."""

    L0_ANECDOTAL = "L0"
    L1_BEFORE_AFTER = "L1"
    L2_MATCHED_DESCRIPTIVE = "L2"
    L3_DID_VALIDATED = "L3"
    L4_RANDOMIZED = "L4"
    L5_POLICY_READY = "L5"


_EVIDENCE_RANK: dict[EvidenceLevel, int] = {
    EvidenceLevel.L0_ANECDOTAL: 0,
    EvidenceLevel.L1_BEFORE_AFTER: 1,
    EvidenceLevel.L2_MATCHED_DESCRIPTIVE: 2,
    EvidenceLevel.L3_DID_VALIDATED: 3,
    EvidenceLevel.L4_RANDOMIZED: 4,
    EvidenceLevel.L5_POLICY_READY: 5,
}

# Minimum rank at which an effect / causal claim is permitted (ODP-ML-05 §5, CI-008).
EFFECT_CLAIM_MIN_RANK = 1  # L1: outcome observed over a matured window
CAUSAL_CLAIM_MIN_RANK = 3  # L3: matched control + passing pre-trend


class Recommendation(StrEnum):
    CONTINUE = "CONTINUE"
    SCALE = "SCALE"
    STOP = "STOP"
    CHANGE_CHANNEL = "CHANGE_CHANNEL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class ObservationWindowSpec:
    """Outcome + maturity window definition attached at case creation.

    ``outcome_window_days`` is the evaluation horizon; ``maturity_buffer_days``
    is the extra settle time for transactions, refunds and cost data before a
    label is mature (ODP-ML-05 §6). The concrete window (with ``opened_at``) is
    only instantiated at execution, so a window can never mature before its
    intervention has executed.
    """

    outcome_window_days: int
    maturity_buffer_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_window_days": self.outcome_window_days,
            "maturity_buffer_days": self.maturity_buffer_days,
        }


# Default windows per treatment (ODP-ML-05 §6.2): (outcome_window_days, maturity_buffer_days).
DEFAULT_WINDOWS: dict[InterventionKind, ObservationWindowSpec] = {
    InterventionKind.PRICE_CHANGE: ObservationWindowSpec(21, 7),
    InterventionKind.AD_CAMPAIGN: ObservationWindowSpec(14, 7),
    InterventionKind.PROMOTION: ObservationWindowSpec(7, 7),
    InterventionKind.CRM_RECALL: ObservationWindowSpec(21, 7),
    InterventionKind.MAINTENANCE: ObservationWindowSpec(10, 3),
    InterventionKind.CLEANING: ObservationWindowSpec(21, 7),
    InterventionKind.OPENING_CAMPAIGN: ObservationWindowSpec(28, 7),
    InterventionKind.EXTERNAL_SHOCK: ObservationWindowSpec(28, 7),
}


def default_window_for(kind: InterventionKind) -> ObservationWindowSpec:
    return DEFAULT_WINDOWS.get(kind, ObservationWindowSpec(14, 7))


class InterventionError(ValueError):
    """Raised on an invalid lifecycle transition or a violated guardrail."""


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    checked_at: datetime
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "checked_at": self.checked_at.isoformat(),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ConflictResult:
    has_conflict: bool
    checked_at: datetime
    conflicting_ids: tuple[str, ...] = ()
    conflicting_kinds: tuple[str, ...] = ()
    resolved: bool = False
    resolution_reason: str = ""

    @property
    def blocks_approval(self) -> bool:
        """An unresolved conflict blocks the case from reaching approval."""
        return self.has_conflict and not self.resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_conflict": self.has_conflict,
            "checked_at": self.checked_at.isoformat(),
            "conflicting_ids": list(self.conflicting_ids),
            "conflicting_kinds": list(self.conflicting_kinds),
            "resolved": self.resolved,
            "resolution_reason": self.resolution_reason,
            "blocks_approval": self.blocks_approval,
        }


@dataclass(frozen=True)
class ApprovalRecord:
    """Decision output contract (ODP-MOD-05 §5.1.2)."""

    approved: bool
    actor_id: str
    decision_reason: str
    approved_at: datetime
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "actor_id": self.actor_id,
            "decision_reason": self.decision_reason,
            "approved_at": self.approved_at.isoformat(),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    """Execution output contract (ODP-MOD-05 §5.1.3)."""

    execution_id: str
    executor: str
    executed_at: datetime
    status: str
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "executor": self.executor,
            "executed_at": self.executed_at.isoformat(),
            "status": self.status,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class ObservationWindow:
    """Concrete observation window, opened at execution time.

    ``maturity_time`` (the label maturity time) is always strictly after
    ``opened_at`` because both window lengths are positive, so a window can never
    be mature before its intervention executes.
    """

    opened_at: datetime
    outcome_window_days: int
    maturity_buffer_days: int

    @property
    def outcome_window_end(self) -> datetime:
        return self.opened_at + timedelta(days=self.outcome_window_days)

    @property
    def maturity_time(self) -> datetime:
        return self.outcome_window_end + timedelta(days=self.maturity_buffer_days)

    def is_mature(self, *, now: datetime) -> bool:
        return now >= self.maturity_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "opened_at": self.opened_at.isoformat(),
            "outcome_window_days": self.outcome_window_days,
            "maturity_buffer_days": self.maturity_buffer_days,
            "outcome_window_end": self.outcome_window_end.isoformat(),
            "label_maturity_time": self.maturity_time.isoformat(),
        }


@dataclass(frozen=True)
class InterventionOutcome:
    """Raw measured outcome (ODP-MOD-05 §5.1.4, ODP-ML-05 §15.2)."""

    collected_at: datetime
    incremental_revenue: float
    incremental_gross_margin: float
    has_control_group: bool
    pretrend_status: PretrendStatus
    treatment_store_count: int
    control_store_count: int
    evaluation_method: EvaluationMethod
    randomized: bool = False
    ad_spend: float = 0.0
    measurement_method: str = MEASUREMENT_METHOD_DEFAULT

    @property
    def iromi(self) -> float | None:
        """Incremental ROMI = Incremental GM / Ad Spend (ODP-ML-05 §4)."""
        if self.ad_spend <= 0:
            return None
        return self.incremental_gross_margin / self.ad_spend

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at.isoformat(),
            "incremental_revenue": self.incremental_revenue,
            "incremental_gross_margin": self.incremental_gross_margin,
            "has_control_group": self.has_control_group,
            "pretrend_status": self.pretrend_status.value,
            "treatment_store_count": self.treatment_store_count,
            "control_store_count": self.control_store_count,
            "evaluation_method": self.evaluation_method.value,
            "randomized": self.randomized,
            "ad_spend": self.ad_spend,
            "iromi": self.iromi,
            "measurement_method": self.measurement_method,
        }


@dataclass(frozen=True)
class EffectEvaluation:
    """Effect evaluation + evidence (ODP-ML-05 §18)."""

    evaluated_at: datetime
    evidence_level: EvidenceLevel
    can_claim_effect: bool
    can_claim_causal: bool
    incremental_revenue: float
    incremental_gross_margin: float
    iromi: float | None
    evaluation_method: EvaluationMethod
    pretrend_status: PretrendStatus
    recommendation: Recommendation
    observation_mature: bool
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "evidence_level": self.evidence_level.value,
            "can_claim_effect": self.can_claim_effect,
            "can_claim_causal": self.can_claim_causal,
            "incremental_revenue": self.incremental_revenue,
            "incremental_gross_margin": self.incremental_gross_margin,
            "iromi": self.iromi,
            "evaluation_method": self.evaluation_method.value,
            "pretrend_status": self.pretrend_status.value,
            "recommendation": self.recommendation.value,
            "observation_mature": self.observation_mature,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class LabelRecord:
    """Label written back to the Label Registry for ForecastOps consumption.

    Intervened periods must be excluded from (or marked in) the organic forecast
    baseline so the model never learns an intervention effect as seasonality
    (ODP-MOD-05 AC-05-05).
    """

    intervention_id: str
    store_id: str
    treatment_type: str
    outcome_window_start: datetime
    outcome_window_end: datetime
    label_maturity_time: datetime
    is_mature: bool
    evidence_level: EvidenceLevel
    incremental_revenue: float
    incremental_gross_margin: float
    iromi: float | None
    measurement_method: str
    can_claim_effect: bool
    can_claim_causal: bool
    exclude_from_baseline: bool = True
    written_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention_id,
            "store_id": self.store_id,
            "treatment_type": self.treatment_type,
            "outcome_window": {
                "start": self.outcome_window_start.isoformat(),
                "end": self.outcome_window_end.isoformat(),
            },
            "label_maturity_time": self.label_maturity_time.isoformat(),
            "is_mature": self.is_mature,
            "evidence_level": self.evidence_level.value,
            "incremental_revenue": self.incremental_revenue,
            "incremental_gross_margin": self.incremental_gross_margin,
            "iromi": self.iromi,
            "measurement_method": self.measurement_method,
            "can_claim_effect": self.can_claim_effect,
            "can_claim_causal": self.can_claim_causal,
            "exclude_from_baseline": self.exclude_from_baseline,
            "written_at": self.written_at.isoformat(),
        }


@dataclass(frozen=True)
class InterventionTransition:
    from_status: InterventionStatus
    to_status: InterventionStatus
    actor: str
    action: str
    reason: str
    at: datetime
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "actor": self.actor,
            "action": self.action,
            "reason": self.reason,
            "at": self.at.isoformat(),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class Intervention:
    """The intervention case aggregate."""

    intervention_id: str
    store_id: str
    kind: InterventionKind
    status: InterventionStatus
    trigger_ref: str
    expected_outcome: str
    planned_start: datetime
    planned_end: datetime
    window_spec: ObservationWindowSpec
    created_by: str
    created_at: datetime
    policy_version: str = POLICY_VERSION
    action_spec: dict[str, Any] = field(default_factory=dict)
    eligibility: EligibilityResult | None = None
    conflict: ConflictResult | None = None
    approval: ApprovalRecord | None = None
    execution: ExecutionRecord | None = None
    observation_window: ObservationWindow | None = None
    outcome: InterventionOutcome | None = None
    effect: EffectEvaluation | None = None
    history: tuple[InterventionTransition, ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def effective_window_end(self) -> datetime:
        """Window end used for overlap detection: extends through the maturity
        time once a real observation window exists (cooldown contamination)."""
        if self.observation_window is not None:
            return max(self.planned_end, self.observation_window.maturity_time)
        return self.planned_end

    def with_transition(
        self,
        *,
        to_status: InterventionStatus,
        actor: str,
        action: str,
        reason: str,
        correlation_id: str = "",
        **updates: Any,
    ) -> Intervention:
        transition = InterventionTransition(
            from_status=self.status,
            to_status=to_status,
            actor=actor,
            action=action,
            reason=reason,
            at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        return replace(
            self,
            status=to_status,
            history=(*self.history, transition),
            **updates,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention_id,
            "store_id": self.store_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "trigger_ref": self.trigger_ref,
            "expected_outcome": self.expected_outcome,
            "planned_start": self.planned_start.isoformat(),
            "planned_end": self.planned_end.isoformat(),
            "window_spec": self.window_spec.to_dict(),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "policy_version": self.policy_version,
            "action_spec": dict(self.action_spec),
            "eligibility": self.eligibility.to_dict() if self.eligibility else None,
            "conflict": self.conflict.to_dict() if self.conflict else None,
            "approval": self.approval.to_dict() if self.approval else None,
            "execution": self.execution.to_dict() if self.execution else None,
            "observation_window": (
                self.observation_window.to_dict() if self.observation_window else None
            ),
            "outcome": self.outcome.to_dict() if self.outcome else None,
            "effect": self.effect.to_dict() if self.effect else None,
            "history": [transition.to_dict() for transition in self.history],
        }


def new_intervention(
    *,
    store_id: str,
    kind: InterventionKind,
    trigger_ref: str,
    expected_outcome: str,
    planned_start: datetime,
    planned_end: datetime,
    created_by: str,
    window_spec: ObservationWindowSpec | None = None,
    action_spec: dict[str, Any] | None = None,
    policy_version: str = POLICY_VERSION,
    intervention_id: str | None = None,
) -> Intervention:
    """Create a CANDIDATE intervention.

    AC-05-01: every intervention is created with a start time, an end time, an
    observation-window definition and an outcome definition.
    """
    if planned_end <= planned_start:
        raise InterventionError("planned_end must be after planned_start")
    if not expected_outcome.strip():
        raise InterventionError("expected_outcome (outcome definition) is required")
    return Intervention(
        intervention_id=intervention_id or f"intervention-{uuid4()}",
        store_id=store_id,
        kind=kind,
        status=InterventionStatus.CANDIDATE,
        trigger_ref=trigger_ref,
        expected_outcome=expected_outcome,
        planned_start=planned_start,
        planned_end=planned_end,
        window_spec=window_spec or default_window_for(kind),
        created_by=created_by,
        created_at=datetime.now(UTC),
        policy_version=policy_version,
        action_spec=dict(action_spec or {}),
    )


def windows_overlap(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> bool:
    return start_a <= end_b and start_b <= end_a


def detect_conflicts(
    candidate: Intervention, others: list[Intervention]
) -> tuple[Intervention, ...]:
    """Same-store interventions whose effective window overlaps the candidate's.

    Overlapping interventions contaminate one another's outcome attribution
    (ODP-ML-05 §7, §8.3), so they must be surfaced — never silently overwritten
    (ODP-MOD-05 AC-05-02 / ODP-ML-05 CI-003).
    """
    conflicts: list[Intervention] = []
    for other in others:
        if other.intervention_id == candidate.intervention_id:
            continue
        if other.store_id != candidate.store_id:
            continue
        if other.status not in ACTIVE_CONFLICT_STATUSES:
            continue
        if windows_overlap(
            candidate.planned_start,
            candidate.effective_window_end(),
            other.planned_start,
            other.effective_window_end(),
        ):
            conflicts.append(other)
    return tuple(conflicts)


def resolve_evidence_level(
    *,
    mature: bool,
    has_control_group: bool,
    pretrend_status: PretrendStatus,
    randomized: bool,
    replicated: bool = False,
) -> EvidenceLevel:
    """Map outcome conditions to an Evidence Level (ODP-ML-05 §5, §8.3).

    - Not mature → L0: before/after over an immature window is anecdotal.
    - No control group → at most L1 (before/after).
    - Control but pre-trend not passing → at most L2 (matched descriptive).
    - Control + passing pre-trend → L3 (DiD validated), or L4 if randomized.
    - Replicated / policy-ready can promote L3/L4 to L5.
    """
    if not mature:
        return EvidenceLevel.L0_ANECDOTAL
    if not has_control_group:
        return EvidenceLevel.L1_BEFORE_AFTER
    if pretrend_status is not PretrendStatus.PASS:
        return EvidenceLevel.L2_MATCHED_DESCRIPTIVE
    base = EvidenceLevel.L4_RANDOMIZED if randomized else EvidenceLevel.L3_DID_VALIDATED
    if replicated:
        return EvidenceLevel.L5_POLICY_READY
    return base


def evidence_rank(level: EvidenceLevel) -> int:
    return _EVIDENCE_RANK[level]


def can_claim_effect(level: EvidenceLevel) -> bool:
    return evidence_rank(level) >= EFFECT_CLAIM_MIN_RANK


def can_claim_causal(level: EvidenceLevel) -> bool:
    return evidence_rank(level) >= CAUSAL_CLAIM_MIN_RANK


__all__ = [
    "ACTIVE_CONFLICT_STATUSES",
    "CAUSAL_CLAIM_MIN_RANK",
    "DEFAULT_WINDOWS",
    "EFFECT_CLAIM_MIN_RANK",
    "FEATURE_VERSION",
    "MEASUREMENT_METHOD_DEFAULT",
    "POLICY_VERSION",
    "TERMINAL_STATUSES",
    "ApprovalRecord",
    "ConflictResult",
    "EffectEvaluation",
    "EligibilityResult",
    "EvaluationMethod",
    "EvidenceLevel",
    "ExecutionRecord",
    "Intervention",
    "InterventionError",
    "InterventionKind",
    "InterventionOutcome",
    "InterventionStatus",
    "InterventionTransition",
    "LabelRecord",
    "ObservationWindow",
    "ObservationWindowSpec",
    "PretrendStatus",
    "Recommendation",
    "can_claim_causal",
    "can_claim_effect",
    "default_window_for",
    "detect_conflicts",
    "evidence_rank",
    "new_intervention",
    "resolve_evidence_level",
    "windows_overlap",
]
