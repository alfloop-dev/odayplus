"""Evaluate policy-declared work requirements against server-owned records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from modules.listing.application.intake_workflow_bootstrap import IntakeWorkflowUnavailable


class IntakeWorkIncomplete(RuntimeError):
    """Required work has not committed; callers return WORK_INCOMPLETE."""


TERMINAL_WORK_STATES = {
    "intake": {"READY", "CANCELLED"},
    "decision": {"EXECUTED", "REJECTED", "REVERSED", "SUPERSEDED"},
    "correction": {"APPLIED", "REJECTED"},
    "promotion": {"COMPLETED", "REJECTED"},
    "job": {"SUCCEEDED"},
}
COLLECTIONS = {
    "decision": "decisions",
    "correction": "corrections",
    "promotion": "promotions",
    "job": "jobs",
}
IDS = {
    "intake": "intake_id",
    "decision": "decision_id",
    "correction": "correction_id",
    "promotion": "promotion_decision_id",
    "job": "job_id",
}


@dataclass(frozen=True)
class CompletionRequirement:
    kind: str
    allowed_states: frozenset[str]
    minimum_count: int
    require_audit: bool
    action: str | None

    @classmethod
    def parse(cls, value: Any) -> CompletionRequirement:
        if not isinstance(value, Mapping):
            raise IntakeWorkflowUnavailable("Completion requirement must be an object")
        kind, states = value.get("kind"), value.get("allowed_states")
        count, audited = value.get("minimum_count"), value.get("require_audit")
        if (
            kind not in TERMINAL_WORK_STATES
            or not isinstance(states, list)
            or not states
            or any(not isinstance(x, str) for x in states)
            or not set(states) <= TERMINAL_WORK_STATES[kind]
            or type(count) is not int
            or count < 1
            or type(audited) is not bool
        ):
            raise IntakeWorkflowUnavailable("Completion requirement is incomplete or nonterminal")
        action = value.get("action")
        if action is not None and (
            kind not in {"decision", "job"} or not isinstance(action, str) or not action.strip()
        ):
            raise IntakeWorkflowUnavailable("Invalid completion work selector")
        return cls(kind, frozenset(states), count, audited, action)


def verify_completion(
    store: Any, intake: Mapping[str, Any], requirements: Any
) -> list[dict[str, Any]]:
    """No request supplies completion booleans, evidence rows or allowed states."""
    if not isinstance(requirements, (tuple, list)) or not requirements:
        raise IntakeWorkflowUnavailable("Explicit completion requirements are unavailable")
    intake_id, tenant_id = intake["intake_id"], intake["scope"]["tenant_id"]
    proofs = []
    for requirement in requirements:
        if not isinstance(requirement, CompletionRequirement):
            raise IntakeWorkflowUnavailable("Completion requirement was not resolved")
        candidates = (
            [intake]
            if requirement.kind == "intake"
            else [
                row
                for row in getattr(store, COLLECTIONS[requirement.kind]).values()
                if row.get("intake_id") == intake_id
                and (
                    requirement.action is None
                    or row.get("action", row.get("job_type")) == requirement.action
                )
            ]
        )
        if len(candidates) < requirement.minimum_count:
            raise IntakeWorkIncomplete("Required committed work is missing")
        for row in candidates:
            scope = row.get("scope")
            row_tenant = row.get(
                "tenant_id", scope.get("tenant_id") if isinstance(scope, Mapping) else None
            )
            if row_tenant != tenant_id or (
                scope is not None and dict(scope) != dict(intake["scope"])
            ):
                raise IntakeWorkIncomplete("Work evidence is outside the Intake tenant/scope")
            state = row.get("state") if requirement.kind == "intake" else row.get("status")
            if (
                state not in requirement.allowed_states
                or type(row.get("version")) is not int
                or row["version"] < 1
            ):
                raise IntakeWorkIncomplete("Required work is not completed")
            record_id = row.get(IDS[requirement.kind])
            try:
                if not isinstance(record_id, str):
                    raise ValueError("Record identity must be a UUID string")
                UUID(record_id)
            except (ValueError, TypeError) as exc:
                raise IntakeWorkIncomplete("Work evidence has no canonical identity") from exc
            if requirement.require_audit:
                try:
                    UUID(row.get("audit_event_id"))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise IntakeWorkIncomplete("Work audit identity is invalid") from exc
                audit = store.workflow_audits.get(row.get("audit_event_id"))
                if (
                    not audit
                    or audit.get("audit_event_id") != row.get("audit_event_id")
                    or audit.get("tenant_id") != tenant_id
                    or audit.get("intake_id") != intake_id
                    or audit.get("resource_type")
                    not in {
                        "intake": {"intake"},
                        "decision": {"decision", "identity_decision", "match_decision"},
                        "correction": {"correction"},
                        "promotion": {"promotion_decision"},
                        "job": {"job"},
                    }[requirement.kind]
                    or audit.get("resource_id") != record_id
                    or audit.get("result") != "SUCCEEDED"
                    or audit.get("after_value", {}).get("version") != row["version"]
                    or audit.get("after_value", {}).get(
                        "state" if requirement.kind == "intake" else "status"
                    )
                    != state
                ):
                    raise IntakeWorkIncomplete("Required work has no current bound audit evidence")
            proofs.append(
                {
                    "resource_type": requirement.kind,
                    "resource_id": record_id,
                    "version": row["version"],
                    "state": state,
                    "audit_event_id": row.get("audit_event_id"),
                }
            )
    # A satisfied row cannot hide another pending review for the same Intake.
    for kind in ("decision", "correction", "promotion"):
        for row in getattr(store, COLLECTIONS[kind]).values():
            if (
                row.get("intake_id") == intake_id
                and row.get("status") not in TERMINAL_WORK_STATES[kind]
            ):
                raise IntakeWorkIncomplete(
                    "An associated decision/correction/review remains unresolved"
                )
    return proofs
