"""Listing public API, loaded lazily to keep domain imports side-effect free."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CandidateSiteDraft",
    "InMemoryListingRepository",
    "ListingDuplicateGroup",
    "ListingHardRulePolicy",
    "ListingImportResult",
    "ListingPipeline",
    "ListingPipelineRecord",
    "run_listing_csv_import",
    "ListingPipelineStatus",
    "run_listing_import",
    # Intake state symbols
    "IntakeStage",
    "ListingState",
    "IdentityGraphState",
    "AssignmentState",
    "SlaState",
    "PromotionState",
    "PrincipalRole",
    "DenialCode",
    "DomainValidationError",
    "Actor",
    "TransitionContext",
    "IntakeAggregate",
    "ListingAggregate",
    "IdentityDecisionAggregate",
    "AssignmentAggregate",
    "SlaInstanceAggregate",
    "PromotionAggregate",
    "IntakeStateMachine",
    "ListingStateMachine",
    "IdentityDecisionStateMachine",
    "AssignmentStateMachine",
    "SlaStateMachine",
    "PromotionStateMachine",
    "IntakeWorkflowService",
    "InMemoryIntakeRepository",
    "AssignmentSlaService",
    "InMemoryAssignmentRepository",
    "InMemorySlaRepository",
]

_EXPORT_MODULES = {
    "CandidateSiteDraft": "modules.listing.domain.models",
    "InMemoryListingRepository": "modules.listing.infrastructure.repositories",
    "ListingDuplicateGroup": "modules.listing.domain.models",
    "ListingHardRulePolicy": "modules.listing.domain.models",
    "ListingImportResult": "modules.listing.application.pipeline",
    "ListingPipeline": "modules.listing.application.pipeline",
    "ListingPipelineRecord": "modules.listing.application.pipeline",
    "ListingPipelineStatus": "modules.listing.domain.models",
    "run_listing_csv_import": "modules.listing.application.pipeline",
    "run_listing_import": "modules.listing.application.pipeline",
    # Intake state machine symbols
    "IntakeStage": "modules.listing.domain.intake_states",
    "ListingState": "modules.listing.domain.intake_states",
    "IdentityGraphState": "modules.listing.domain.intake_states",
    "AssignmentState": "modules.listing.domain.intake_states",
    "SlaState": "modules.listing.domain.intake_states",
    "PromotionState": "modules.listing.domain.intake_states",
    "PrincipalRole": "modules.listing.domain.intake_states",
    "DenialCode": "modules.listing.domain.intake_states",
    "DomainValidationError": "modules.listing.domain.intake_states",
    "Actor": "modules.listing.domain.intake_states",
    "TransitionContext": "modules.listing.domain.intake_states",
    "IntakeAggregate": "modules.listing.domain.intake_states",
    "ListingAggregate": "modules.listing.domain.intake_states",
    "IdentityDecisionAggregate": "modules.listing.domain.intake_states",
    "AssignmentAggregate": "modules.listing.domain.intake_states",
    "SlaInstanceAggregate": "modules.listing.domain.intake_states",
    "PromotionAggregate": "modules.listing.domain.intake_states",
    "IntakeStateMachine": "modules.listing.domain.intake_states",
    "ListingStateMachine": "modules.listing.domain.intake_states",
    "IdentityDecisionStateMachine": "modules.listing.domain.intake_states",
    "AssignmentStateMachine": "modules.listing.domain.intake_states",
    "SlaStateMachine": "modules.listing.domain.intake_states",
    "PromotionStateMachine": "modules.listing.domain.intake_states",
    "IntakeWorkflowService": "modules.listing.application.intake_workflow",
    "InMemoryIntakeRepository": "modules.listing.application.intake_workflow",
    "AssignmentSlaService": "modules.listing.application.assignment_sla",
    "InMemoryAssignmentRepository": "modules.listing.application.assignment_sla",
    "InMemorySlaRepository": "modules.listing.application.assignment_sla",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
