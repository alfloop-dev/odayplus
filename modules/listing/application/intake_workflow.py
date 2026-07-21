from __future__ import annotations

from typing import Any, Protocol

from modules.listing.domain.intake_states import (
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    TransitionContext,
)


class IntakeRepository(Protocol):
    def get_by_id(self, intake_id: str) -> IntakeAggregate | None:
        ...

    def save(self, intake: IntakeAggregate) -> None:
        ...


class InMemoryIntakeRepository:
    def __init__(self) -> None:
        self._store: dict[str, IntakeAggregate] = {}

    def get_by_id(self, intake_id: str) -> IntakeAggregate | None:
        return self._store.get(intake_id)

    def save(self, intake: IntakeAggregate) -> None:
        self._store[intake.id] = intake


class IntakeWorkflowService:
    """Orchestrates intake stage transitions and emits audit event info."""

    def __init__(self, repository: IntakeRepository) -> None:
        self.repository = repository
        self.emitted_events: list[dict[str, Any]] = []

    def _record_event(self, event_type: str, intake_id: str, tenant_id: str, correlation_id: str | None, payload: dict[str, Any]) -> None:
        self.emitted_events.append({
            "event_type": event_type,
            "intake_id": intake_id,
            "tenant_id": tenant_id,
            "correlation_id": correlation_id,
            "payload": payload,
        })

    def submit_intake(self, intake_id: str, tenant_id: str, source_id: str, canonical_url: str | None, context: TransitionContext) -> IntakeAggregate:
        # State Machine Validation for None -> SUBMITTED
        IntakeStateMachine.transition(None, IntakeStage.SUBMITTED, context)

        intake = IntakeAggregate(
            id=intake_id,
            tenant_id=tenant_id,
            stage=IntakeStage.SUBMITTED,
            version=1,
            created_by=context.actor.actor_id,
            source_id=source_id,
            canonical_url=canonical_url,
            owner_id=context.actor.actor_id,
            idempotency_key=context.idempotency_key,
        )
        self.repository.save(intake)

        self._record_event(
            "intake.submitted.v1",
            intake_id,
            tenant_id,
            context.correlation_id,
            {"url": canonical_url, "source_id": source_id}
        )
        return intake

    def start_identity_check(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.CHECKING_IDENTITY, context)
        self.repository.save(intake)

        self._record_event(
            "intake.identity_check_started.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"canonical_url": intake.canonical_url}
        )
        return intake

    def resolve_identity_exact(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.READY, context)
        self.repository.save(intake)

        self._record_event(
            "intake.resolved.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"outcome": "EXACT_MATCH"}
        )
        return intake

    def start_source_policy_evaluation(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.CHECKING_SOURCE_POLICY, context)
        self.repository.save(intake)

        self._record_event(
            "intake.source_policy_started.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"source_id": intake.source_id}
        )
        return intake

    def approve_retrieval(self, intake_id: str, policy: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.RETRIEVING, context)
        intake.policy = policy
        self.repository.save(intake)

        self._record_event(
            "intake.retrieval_approved.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"policy": policy}
        )
        return intake

    def require_assisted_entry(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.AWAITING_ASSISTED_ENTRY, context)
        self.repository.save(intake)

        self._record_event(
            "intake.assisted_entry_required.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {}
        )
        return intake

    def quarantine_policy(self, intake_id: str, reason: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.QUARANTINED, context)
        intake.evidence["quarantine_reason"] = reason
        self.repository.save(intake)

        self._record_event(
            "intake.quarantined.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": reason, "stage_before": "CHECKING_SOURCE_POLICY"}
        )
        return intake

    def start_parsing_from_retrieval(self, intake_id: str, snapshot_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.PARSING, context)
        intake.evidence["snapshot_id"] = snapshot_id
        self.repository.save(intake)

        self._record_event(
            "snapshot.created.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"snapshot_id": snapshot_id}
        )
        self._record_event(
            "intake.parsing_started.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"snapshot_id": snapshot_id}
        )
        return intake

    def fail_retrieval(self, intake_id: str, error_code: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.FAILED, context)
        intake.evidence["error_code"] = error_code
        self.repository.save(intake)

        self._record_event(
            "intake.processing_failed.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"error_code": error_code, "stage_before": "RETRIEVING"}
        )
        return intake

    def quarantine_retrieval(self, intake_id: str, reason: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.QUARANTINED, context)
        intake.evidence["quarantine_reason"] = reason
        self.repository.save(intake)

        self._record_event(
            "intake.quarantined.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": reason, "stage_before": "RETRIEVING"}
        )
        return intake

    def complete_assisted_entry(self, intake_id: str, corrections: dict[str, Any], context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.PARSING, context)
        intake.evidence["corrections"] = corrections
        self.repository.save(intake)

        self._record_event(
            "intake.assisted_entry_completed.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"corrections": corrections}
        )
        return intake

    def complete_parsing(self, intake_id: str, parser_run_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.MATCHING, context)
        intake.evidence["parser_run_id"] = parser_run_id
        self.repository.save(intake)

        self._record_event(
            "parser.run_completed.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"parser_run_id": parser_run_id}
        )
        self._record_event(
            "intake.matching_started.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {}
        )
        return intake

    def route_review_from_parsing(self, intake_id: str, warnings: list[str], context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.NEEDS_REVIEW, context)
        intake.evidence["warnings"] = warnings
        self.repository.save(intake)

        self._record_event(
            "intake.review_required.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"warnings": warnings}
        )
        return intake

    def fail_parsing(self, intake_id: str, error_code: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.FAILED, context)
        intake.evidence["error_code"] = error_code
        self.repository.save(intake)

        self._record_event(
            "intake.processing_failed.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"error_code": error_code, "stage_before": "PARSING"}
        )
        return intake

    def resolve_match(self, intake_id: str, outcome: str, target_listing_id: str | None, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.READY, context)
        intake.evidence["match_outcome"] = outcome
        intake.evidence["target_listing_id"] = target_listing_id
        self.repository.save(intake)

        self._record_event(
            "match.decided.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"outcome": outcome, "target_listing_id": target_listing_id}
        )
        self._record_event(
            "intake.resolved.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {}
        )
        return intake

    def route_review_from_matching(self, intake_id: str, candidates: list[str], context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.NEEDS_REVIEW, context)
        intake.evidence["match_candidates"] = candidates
        self.repository.save(intake)

        self._record_event(
            "match.review_required.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"candidates": candidates}
        )
        return intake

    def quarantine_matching(self, intake_id: str, reason: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.QUARANTINED, context)
        intake.evidence["quarantine_reason"] = reason
        self.repository.save(intake)

        self._record_event(
            "intake.quarantined.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": reason, "stage_before": "MATCHING"}
        )
        return intake

    def decide_review(self, intake_id: str, decision_action: str, reason: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.READY, context)
        intake.evidence["review_decision"] = decision_action
        intake.evidence["review_reason"] = reason
        self.repository.save(intake)

        self._record_event(
            "match.decided.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"decision_action": decision_action, "reason": reason}
        )
        self._record_event(
            "intake.resolved.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {}
        )
        return intake

    def quarantine_review(self, intake_id: str, reason: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.QUARANTINED, context)
        intake.evidence["quarantine_reason"] = reason
        self.repository.save(intake)

        self._record_event(
            "intake.quarantined.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": reason, "stage_before": "NEEDS_REVIEW"}
        )
        return intake

    def reopen_quarantine_policy(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.CHECKING_SOURCE_POLICY, context)
        self.repository.save(intake)

        self._record_event(
            "intake.reopened.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": context.reason, "stage_before": "QUARANTINED", "stage_after": "CHECKING_SOURCE_POLICY"}
        )
        return intake

    def reopen_quarantine_review(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.NEEDS_REVIEW, context)
        self.repository.save(intake)

        self._record_event(
            "intake.reopened.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": context.reason, "stage_before": "QUARANTINED", "stage_after": "NEEDS_REVIEW"}
        )
        return intake

    def replay_failed_job(self, intake_id: str, target: IntakeStage, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        # target must be RETRIEVING or PARSING
        if target not in {IntakeStage.RETRIEVING, IntakeStage.PARSING}:
            raise ValueError(f"Invalid replay target state {target}")

        IntakeStateMachine.transition(intake, target, context)
        self.repository.save(intake)

        self._record_event(
            "job.replay_requested.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"replay_target": target.value}
        )
        return intake

    def cancel_intake(self, intake_id: str, context: TransitionContext) -> IntakeAggregate:
        intake = self.repository.get_by_id(intake_id)
        if not intake:
            raise ValueError(f"Intake {intake_id} not found")

        IntakeStateMachine.transition(intake, IntakeStage.CANCELLED, context)
        intake.is_cancelled = True
        self.repository.save(intake)

        self._record_event(
            "intake.cancelled.v1",
            intake_id,
            intake.tenant_id,
            context.correlation_id,
            {"reason": context.reason}
        )
        return intake
