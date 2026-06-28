"""InterventionOps lifecycle workflow engine.

A single state machine drives every treatment type (price, ad, promotion, CRM,
maintenance, cleaning). It enforces the ODP-MOD-05 / ODP-ML-05 guardrails:

- approval and execution are separate steps (no optimistic auto-execution);
- an unresolved conflict blocks approval (overlap is surfaced, never silently
  overwritten);
- the observation window only opens at execution, so it can never mature early;
- effect / causal claims are gated on observation maturity and a valid control
  group, and every evaluation carries an Evidence Level;
- a matured effect evaluation writes a label back to the Label Registry so
  ForecastOps can exclude or mark the intervened period.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from modules.intervention.domain.lifecycle import (
    POLICY_VERSION,
    ApprovalRecord,
    ConflictResult,
    EffectEvaluation,
    EligibilityResult,
    EvaluationMethod,
    ExecutionRecord,
    Intervention,
    InterventionError,
    InterventionKind,
    InterventionOutcome,
    InterventionStatus,
    LabelRecord,
    ObservationWindow,
    ObservationWindowSpec,
    PretrendStatus,
    Recommendation,
    can_claim_causal,
    can_claim_effect,
    detect_conflicts,
    new_intervention,
    resolve_evidence_level,
)
from modules.intervention.infrastructure.repositories import (
    InMemoryInterventionRepository,
)
from shared.audit import AuditEvent, InMemoryAuditLog

# iROMI below break-even (incremental GM < ad spend) is a change-channel / stop
# signal for ad campaigns (ODP-ML-05 §15.3).
IROMI_BREAKEVEN = 1.0


class LabelRegistryHook(Protocol):
    def __call__(self, label: LabelRecord) -> None: ...


@dataclass(frozen=True)
class EffectEvaluationOutcome:
    intervention: Intervention
    effect: EffectEvaluation
    label: LabelRecord
    audit_event_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervention_id": self.intervention.intervention_id,
            "status": self.intervention.status.value,
            "effect": self.effect.to_dict(),
            "label": self.label.to_dict(),
            "audit_event_id": self.audit_event_id,
            "intervention": self.intervention.to_dict(),
        }


def _recommend(
    *,
    kind: InterventionKind,
    claim_effect: bool,
    incremental_gross_margin: float,
    iromi: float | None,
) -> Recommendation:
    if not claim_effect:
        return Recommendation.INCONCLUSIVE
    if incremental_gross_margin <= 0:
        return Recommendation.STOP
    if kind is InterventionKind.AD_CAMPAIGN and iromi is not None and iromi < IROMI_BREAKEVEN:
        return Recommendation.CHANGE_CHANNEL
    if incremental_gross_margin > 0 and iromi is not None and iromi >= 2 * IROMI_BREAKEVEN:
        return Recommendation.SCALE
    return Recommendation.CONTINUE


class InterventionWorkflow:
    """State machine for the shared intervention lifecycle."""

    def __init__(
        self,
        *,
        repository: InMemoryInterventionRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        label_hooks: Iterable[LabelRegistryHook] | None = None,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        self.repository = repository or InMemoryInterventionRepository()
        self.audit_log = audit_log or InMemoryAuditLog()
        self.label_hooks: list[LabelRegistryHook] = list(label_hooks or ())
        self.policy_version = policy_version

    def register_label_hook(self, hook: LabelRegistryHook) -> None:
        self.label_hooks.append(hook)

    # -- creation ---------------------------------------------------------

    def open_case(
        self,
        *,
        store_id: str,
        kind: InterventionKind | str,
        trigger_ref: str,
        expected_outcome: str,
        planned_start: datetime,
        planned_end: datetime,
        created_by: str,
        window_spec: ObservationWindowSpec | None = None,
        action_spec: dict[str, Any] | None = None,
        correlation_id: str = "",
        intervention_id: str | None = None,
    ) -> Intervention:
        resolved_kind = InterventionKind(kind)
        intervention = new_intervention(
            store_id=store_id,
            kind=resolved_kind,
            trigger_ref=trigger_ref,
            expected_outcome=expected_outcome,
            planned_start=planned_start,
            planned_end=planned_end,
            created_by=created_by,
            window_spec=window_spec,
            action_spec=action_spec,
            policy_version=self.policy_version,
            intervention_id=intervention_id,
        )
        self.repository.save(intervention)
        self._audit(
            intervention,
            action="create",
            outcome="candidate",
            actor=created_by,
            correlation_id=correlation_id,
            reason=f"intervention candidate {resolved_kind.value}",
        )
        return intervention

    # -- eligibility ------------------------------------------------------

    def check_eligibility(
        self,
        intervention_id: str,
        *,
        eligible: bool,
        actor: str,
        reasons: Iterable[str] = (),
        correlation_id: str = "",
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(
            intervention,
            {InterventionStatus.CANDIDATE, InterventionStatus.ELIGIBILITY_CHECKING},
            "check eligibility",
        )
        result = EligibilityResult(
            eligible=eligible,
            checked_at=datetime.now(UTC),
            reasons=tuple(reasons),
        )
        target = InterventionStatus.ELIGIBLE if eligible else InterventionStatus.INELIGIBLE
        updated = intervention.with_transition(
            to_status=target,
            actor=actor,
            action="check_eligibility",
            reason="eligible" if eligible else "ineligible",
            correlation_id=correlation_id,
            eligibility=result,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="check_eligibility",
            outcome=target.value.lower(),
            actor=actor,
            correlation_id=correlation_id,
            reason=", ".join(result.reasons),
        )
        return updated

    # -- action candidate -------------------------------------------------

    def propose_action(
        self,
        intervention_id: str,
        *,
        action_spec: dict[str, Any],
        actor: str,
        correlation_id: str = "",
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(intervention, {InterventionStatus.ELIGIBLE}, "propose action")
        merged = {**intervention.action_spec, **action_spec}
        updated = intervention.with_transition(
            to_status=InterventionStatus.ACTION_PROPOSED,
            actor=actor,
            action="propose_action",
            reason="action candidate built",
            correlation_id=correlation_id,
            action_spec=merged,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="propose_action",
            outcome="action_proposed",
            actor=actor,
            correlation_id=correlation_id,
            reason="action candidate built",
        )
        return updated

    # -- conflict control -------------------------------------------------

    def check_conflict(
        self,
        intervention_id: str,
        *,
        actor: str,
        allow_overlap: bool = False,
        reason: str = "",
        correlation_id: str = "",
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(
            intervention,
            {InterventionStatus.ACTION_PROPOSED, InterventionStatus.CONFLICT_CHECKING},
            "check conflict",
        )
        others = self.repository.list_by_store(intervention.store_id)
        conflicts = detect_conflicts(intervention, others)
        has_conflict = bool(conflicts)
        if has_conflict and allow_overlap and not reason.strip():
            raise InterventionError(
                "overriding an intervention conflict requires a resolution reason"
            )
        result = ConflictResult(
            has_conflict=has_conflict,
            checked_at=datetime.now(UTC),
            conflicting_ids=tuple(c.intervention_id for c in conflicts),
            conflicting_kinds=tuple(c.kind.value for c in conflicts),
            resolved=(not has_conflict) or allow_overlap,
            resolution_reason=reason if has_conflict and allow_overlap else "",
        )
        updated = intervention.with_transition(
            to_status=InterventionStatus.CONFLICT_CHECKING,
            actor=actor,
            action="check_conflict",
            reason="conflict detected" if has_conflict else "no conflict",
            correlation_id=correlation_id,
            conflict=result,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="check_conflict",
            outcome="conflict_detected" if has_conflict else "no_conflict",
            actor=actor,
            correlation_id=correlation_id,
            reason=reason,
            extra={
                "conflicting_ids": list(result.conflicting_ids),
                "resolved": result.resolved,
            },
        )
        return updated

    # -- approval (separated from execution) ------------------------------

    def submit_for_approval(
        self,
        intervention_id: str,
        *,
        actor: str,
        correlation_id: str = "",
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(
            intervention, {InterventionStatus.CONFLICT_CHECKING}, "submit for approval"
        )
        if intervention.conflict is None:
            raise InterventionError("conflict check must run before approval")
        if intervention.conflict.blocks_approval:
            raise InterventionError(
                "an unresolved conflict blocks approval; resolve the overlap first"
            )
        updated = intervention.with_transition(
            to_status=InterventionStatus.PENDING_APPROVAL,
            actor=actor,
            action="submit_for_approval",
            reason="submitted for approval",
            correlation_id=correlation_id,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="submit_for_approval",
            outcome="pending_approval",
            actor=actor,
            correlation_id=correlation_id,
            reason="submitted for approval",
        )
        return updated

    def approve(
        self,
        intervention_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str = "",
    ) -> Intervention:
        return self._decide(
            intervention_id,
            approved=True,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def reject(
        self,
        intervention_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str = "",
    ) -> Intervention:
        return self._decide(
            intervention_id,
            approved=False,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def _decide(
        self,
        intervention_id: str,
        *,
        approved: bool,
        actor: str,
        reason: str,
        correlation_id: str,
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(
            intervention, {InterventionStatus.PENDING_APPROVAL}, "decide"
        )
        if not reason.strip():
            raise InterventionError(
                "approval and rejection are high-risk and require a reason"
            )
        record = ApprovalRecord(
            approved=approved,
            actor_id=actor,
            decision_reason=reason,
            approved_at=datetime.now(UTC),
            policy_version=self.policy_version,
        )
        target = InterventionStatus.APPROVED if approved else InterventionStatus.REJECTED
        updated = intervention.with_transition(
            to_status=target,
            actor=actor,
            action="approve" if approved else "reject",
            reason=reason,
            correlation_id=correlation_id,
            approval=record,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="approve" if approved else "reject",
            outcome=target.value.lower(),
            actor=actor,
            correlation_id=correlation_id,
            reason=reason,
        )
        return updated

    # -- execution + observation window -----------------------------------

    def execute(
        self,
        intervention_id: str,
        *,
        executor: str,
        correlation_id: str = "",
        executed_at: datetime | None = None,
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(intervention, {InterventionStatus.APPROVED}, "execute")
        if intervention.approval is None or not intervention.approval.approved:
            raise InterventionError("execution requires a recorded approval")
        opened_at = executed_at or datetime.now(UTC)
        execution = ExecutionRecord(
            execution_id=f"intervention-exec-{uuid4()}",
            executor=executor,
            executed_at=opened_at,
            status="EXECUTED",
            correlation_id=correlation_id,
        )
        window = ObservationWindow(
            opened_at=opened_at,
            outcome_window_days=intervention.window_spec.outcome_window_days,
            maturity_buffer_days=intervention.window_spec.maturity_buffer_days,
        )
        executing = intervention.with_transition(
            to_status=InterventionStatus.EXECUTING,
            actor=executor,
            action="execute",
            reason="execution coordinated",
            correlation_id=correlation_id,
            execution=execution,
        )
        observing = executing.with_transition(
            to_status=InterventionStatus.OBSERVING,
            actor=executor,
            action="start_observation",
            reason="observation window opened",
            correlation_id=correlation_id,
            observation_window=window,
        )
        self.repository.save(observing)
        self._audit(
            observing,
            action="execute",
            outcome="observing",
            actor=executor,
            correlation_id=correlation_id,
            reason="execution coordinated",
            extra={
                "execution_id": execution.execution_id,
                "label_maturity_time": window.maturity_time.isoformat(),
            },
        )
        return observing

    # -- outcome collection ----------------------------------------------

    def collect_outcome(
        self,
        intervention_id: str,
        *,
        actor: str,
        incremental_revenue: float,
        incremental_gross_margin: float,
        has_control_group: bool,
        pretrend_status: PretrendStatus | str,
        treatment_store_count: int,
        control_store_count: int,
        evaluation_method: EvaluationMethod | str,
        randomized: bool = False,
        ad_spend: float = 0.0,
        measurement_method: str | None = None,
        correlation_id: str = "",
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(intervention, {InterventionStatus.OBSERVING}, "collect outcome")
        outcome = InterventionOutcome(
            collected_at=datetime.now(UTC),
            incremental_revenue=incremental_revenue,
            incremental_gross_margin=incremental_gross_margin,
            has_control_group=has_control_group,
            pretrend_status=PretrendStatus(pretrend_status),
            treatment_store_count=treatment_store_count,
            control_store_count=control_store_count,
            evaluation_method=EvaluationMethod(evaluation_method),
            randomized=randomized,
            ad_spend=ad_spend,
            measurement_method=measurement_method or "panel_did",
        )
        updated = intervention.with_transition(
            to_status=InterventionStatus.OBSERVING,
            actor=actor,
            action="collect_outcome",
            reason="outcome collected",
            correlation_id=correlation_id,
            outcome=outcome,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action="collect_outcome",
            outcome="outcome_collected",
            actor=actor,
            correlation_id=correlation_id,
            reason="outcome collected",
        )
        return updated

    # -- effect evaluation + label writeback ------------------------------

    def evaluate_effect(
        self,
        intervention_id: str,
        *,
        actor: str,
        now: datetime | None = None,
        replicated: bool = False,
        correlation_id: str = "",
    ) -> EffectEvaluationOutcome:
        intervention = self._require(intervention_id)
        self._require_status(intervention, {InterventionStatus.OBSERVING}, "evaluate effect")
        if intervention.outcome is None:
            raise InterventionError("cannot evaluate effect before an outcome is collected")
        if intervention.observation_window is None:
            raise InterventionError("cannot evaluate effect without an observation window")

        evaluated_at = now or datetime.now(UTC)
        outcome = intervention.outcome
        window = intervention.observation_window
        mature = window.is_mature(now=evaluated_at)

        evidence = resolve_evidence_level(
            mature=mature,
            has_control_group=outcome.has_control_group,
            pretrend_status=outcome.pretrend_status,
            randomized=outcome.randomized,
            replicated=replicated,
        )
        claim_effect = can_claim_effect(evidence)
        claim_causal = can_claim_causal(evidence)

        limitations: list[str] = []
        if not mature:
            limitations.append("observation_window_not_mature")
        if not outcome.has_control_group:
            limitations.append("no_control_group")
        if outcome.pretrend_status is not PretrendStatus.PASS:
            limitations.append(f"pretrend_{outcome.pretrend_status.value.lower()}")

        recommendation = _recommend(
            kind=intervention.kind,
            claim_effect=claim_effect,
            incremental_gross_margin=outcome.incremental_gross_margin,
            iromi=outcome.iromi,
        )

        # Effect figures are only surfaced once an effect may be claimed; an
        # immature window yields a null estimate, never an overstated one.
        effect = EffectEvaluation(
            evaluated_at=evaluated_at,
            evidence_level=evidence,
            can_claim_effect=claim_effect,
            can_claim_causal=claim_causal,
            incremental_revenue=outcome.incremental_revenue if claim_effect else 0.0,
            incremental_gross_margin=outcome.incremental_gross_margin if claim_effect else 0.0,
            iromi=outcome.iromi if claim_effect else None,
            evaluation_method=outcome.evaluation_method,
            pretrend_status=outcome.pretrend_status,
            recommendation=recommendation,
            observation_mature=mature,
            limitations=tuple(limitations),
        )

        evaluating = intervention.with_transition(
            to_status=InterventionStatus.EVALUATING,
            actor=actor,
            action="evaluate_effect",
            reason="effect evaluation started",
            correlation_id=correlation_id,
            effect=effect,
        )
        completed = evaluating.with_transition(
            to_status=InterventionStatus.COMPLETED,
            actor=actor,
            action="complete",
            reason=f"evidence {evidence.value}",
            correlation_id=correlation_id,
        )
        self.repository.save(completed)

        label = LabelRecord(
            intervention_id=completed.intervention_id,
            store_id=completed.store_id,
            treatment_type=completed.kind.value,
            outcome_window_start=window.opened_at,
            outcome_window_end=window.outcome_window_end,
            label_maturity_time=window.maturity_time,
            is_mature=mature,
            evidence_level=evidence,
            incremental_revenue=effect.incremental_revenue,
            incremental_gross_margin=effect.incremental_gross_margin,
            iromi=effect.iromi,
            measurement_method=outcome.measurement_method,
            can_claim_effect=claim_effect,
            can_claim_causal=claim_causal,
        )
        for hook in self.label_hooks:
            hook(label)

        audit_event = self._audit(
            completed,
            action="evaluate_effect",
            outcome="completed",
            actor=actor,
            correlation_id=correlation_id,
            reason=f"evidence {evidence.value}",
            extra={
                "evidence_level": evidence.value,
                "can_claim_effect": claim_effect,
                "can_claim_causal": claim_causal,
                "observation_mature": mature,
                "recommendation": recommendation.value,
                "label_written": len(self.label_hooks) > 0,
            },
        )
        return EffectEvaluationOutcome(
            intervention=completed,
            effect=effect,
            label=label,
            audit_event_id=audit_event.event_id,
        )

    # -- stop / rollback --------------------------------------------------

    def stop(
        self,
        intervention_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str = "",
    ) -> Intervention:
        return self._terminate(
            intervention_id,
            target=InterventionStatus.STOPPED,
            action="stop",
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
            allowed={
                InterventionStatus.APPROVED,
                InterventionStatus.EXECUTING,
                InterventionStatus.OBSERVING,
            },
        )

    def rollback(
        self,
        intervention_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str = "",
    ) -> Intervention:
        return self._terminate(
            intervention_id,
            target=InterventionStatus.ROLLED_BACK,
            action="rollback",
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
            allowed={
                InterventionStatus.EXECUTING,
                InterventionStatus.OBSERVING,
                InterventionStatus.EVALUATING,
            },
        )

    def _terminate(
        self,
        intervention_id: str,
        *,
        target: InterventionStatus,
        action: str,
        actor: str,
        reason: str,
        correlation_id: str,
        allowed: set[InterventionStatus],
    ) -> Intervention:
        intervention = self._require(intervention_id)
        self._require_status(intervention, allowed, action)
        if not reason.strip():
            raise InterventionError(f"{action} requires a reason")
        updated = intervention.with_transition(
            to_status=target,
            actor=actor,
            action=action,
            reason=reason,
            correlation_id=correlation_id,
        )
        self.repository.save(updated)
        self._audit(
            updated,
            action=action,
            outcome=target.value.lower(),
            actor=actor,
            correlation_id=correlation_id,
            reason=reason,
        )
        return updated

    # -- queries ----------------------------------------------------------

    def get(self, intervention_id: str) -> Intervention | None:
        return self.repository.get(intervention_id)

    def list_all(self) -> list[Intervention]:
        return self.repository.list_all()

    def list_by_store(self, store_id: str) -> list[Intervention]:
        return self.repository.list_by_store(store_id)

    # -- internals --------------------------------------------------------

    def _require(self, intervention_id: str) -> Intervention:
        intervention = self.repository.get(intervention_id)
        if intervention is None:
            raise InterventionError(f"unknown intervention {intervention_id}")
        return intervention

    @staticmethod
    def _require_status(
        intervention: Intervention,
        allowed: set[InterventionStatus],
        action: str,
    ) -> None:
        if intervention.status not in allowed:
            raise InterventionError(
                f"cannot {action} on intervention in status {intervention.status.value}"
            )

    def _audit(
        self,
        intervention: Intervention,
        *,
        action: str,
        outcome: str,
        actor: str,
        correlation_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> AuditEvent:
        metadata: dict[str, Any] = {
            "store_id": intervention.store_id,
            "kind": intervention.kind.value,
            "status": intervention.status.value,
            "policy_version": intervention.policy_version,
            "reason": reason,
        }
        if extra:
            metadata.update(extra)
        return self.audit_log.record(
            AuditEvent(
                event_type="intervention.lifecycle.v1",
                actor=actor,
                action=action,
                resource=f"intervention/{intervention.intervention_id}",
                outcome=outcome,
                correlation_id=correlation_id,
                metadata=metadata,
            )
        )


__all__ = [
    "IROMI_BREAKEVEN",
    "EffectEvaluationOutcome",
    "InterventionWorkflow",
    "LabelRegistryHook",
]
