"""Caller snapshots and native transition checks; snapshots grant no authority.

Authorization always uses the current Principal and canonical owner. Recorded
successful receipts bypass fresh snapshot checks, never current authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, StrictInt

from modules.intervention.application.approval_contract import require_positive_intervention_version
from modules.intervention.domain.lifecycle import (
    Intervention,
    InterventionError,
    InterventionStatus,
)
from shared.api.idempotency import request_fingerprint

COHORT_LIMIT = 512
COMMAND_ACTIONS = {
    "assign": "CREATE",
    "unassign": "CREATE",
    "eligibility": "CREATE",
    "action": "CREATE",
    "conflict-check": "CREATE",
    "submit": "CREATE",
    "execute": "EXECUTE",
    "outcomes": "EXECUTE",
    "evaluate": "EXECUTE",
    "close": "APPROVE",
    "adjust": "EXECUTE",
    "stop": "EXECUTE",
    "rollback": "EXECUTE",
}


class LifecycleCommandContext(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: StrictInt = Field(ge=1, le=1)
    command: str = Field(min_length=1)
    intervention_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    actor_subject_id: str = Field(min_length=1)
    version: StrictInt = Field(ge=1)
    policy_version: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cohort_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def lifecycle_command_context(
    intervention: Intervention,
    *,
    command: str,
    tenant_id: str,
    actor_subject_id: str,
    cohort_sha256: str | None = None,
) -> dict[str, Any]:
    require_positive_intervention_version(intervention.version)
    if command not in COMMAND_ACTIONS:
        raise InterventionError("unknown lifecycle command")
    return dict(
        schema_version=1,
        command=command,
        intervention_id=intervention.intervention_id,
        store_id=intervention.store_id,
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        version=intervention.version,
        policy_version=intervention.policy_version,
        snapshot_sha256=request_fingerprint(intervention.to_dict()),
        cohort_sha256=cohort_sha256,
    )


def validate_native_transition(
    original: Intervention, updated: Intervention, *, command: str, actor: str, correlation_id: str
) -> None:
    """Check the actual complete prefix and the native operation's state path."""
    require_positive_intervention_version(original.version)
    require_positive_intervention_version(updated.version)
    prefix = len(original.history)
    tail = updated.history[prefix:]
    paths = {
        "assign": [("assign", original.status)],
        "unassign": [("unassign", original.status)],
        "eligibility": [
            (
                "check_eligibility",
                InterventionStatus.ELIGIBLE
                if updated.eligibility and updated.eligibility.eligible
                else InterventionStatus.INELIGIBLE,
            )
        ],
        "action": [("propose_action", InterventionStatus.ACTION_PROPOSED)],
        "conflict-check": [("check_conflict", InterventionStatus.CONFLICT_CHECKING)],
        "submit": [("submit_for_approval", InterventionStatus.PENDING_APPROVAL)],
        "execute": [
            ("execute", InterventionStatus.EXECUTING),
            ("start_observation", InterventionStatus.OBSERVING),
        ],
        "outcomes": [("collect_outcome", InterventionStatus.OBSERVING)],
        "evaluate": [("evaluate_effect", InterventionStatus.EVALUATING)]
        + (
            [("complete", InterventionStatus.COMPLETED)]
            if updated.effect and updated.effect.observation_mature
            else []
        ),
        "close": [("close", InterventionStatus.CLOSED)],
        "adjust": [("adjust", InterventionStatus.STOPPED)],
        "stop": [("stop", InterventionStatus.STOPPED)],
        "rollback": [("rollback", InterventionStatus.ROLLED_BACK)],
    }
    expected = paths.get(command)
    immutable = (
        "intervention_id",
        "store_id",
        "created_by",
        "created_at",
        "kind",
        "policy_version",
        "predecessor_id",
    )
    if (
        expected is None
        or updated.history[:prefix] != original.history
        or len(tail) != len(expected)
        or updated.version != original.version + len(tail)
        or any(getattr(original, k) != getattr(updated, k) for k in immutable)
    ):
        raise InterventionError("stale update: lifecycle history/version snapshot changed")
    S = InterventionStatus
    allowed = {
        "eligibility": {S.CANDIDATE, S.ELIGIBILITY_CHECKING},
        "action": {S.ELIGIBLE},
        "conflict-check": {S.ACTION_PROPOSED, S.CONFLICT_CHECKING},
        "submit": {S.CONFLICT_CHECKING},
        "execute": {S.APPROVED},
        "outcomes": {S.OBSERVING},
        "evaluate": {S.OBSERVING, S.EVALUATING},
        "close": {S.COMPLETED},
        "adjust": {S.APPROVED, S.EXECUTING, S.OBSERVING},
        "stop": {S.APPROVED, S.EXECUTING, S.OBSERVING},
        "rollback": {S.EXECUTING, S.OBSERVING, S.EVALUATING},
    }
    if (command in {"assign", "unassign"} and original.is_terminal) or (
        command in allowed and original.status not in allowed[command]
    ):
        raise InterventionError(
            "stale update: lifecycle original status is not a legal command source"
        )
    prior = original.status
    for item, (action, status) in zip(tail, expected, strict=True):
        if (
            item.from_status != prior
            or item.to_status != status
            or item.action != action
            or item.actor != actor
            or item.correlation_id != correlation_id
        ):
            raise InterventionError(
                "stale update: lifecycle transition actor or state path changed"
            )
        prior = status
    if updated.status != prior:
        raise InterventionError("stale update: lifecycle final status changed")


@dataclass
class _VerifiedLifecycleTransition:
    original: Intervention
    raw: bytes
    engine: Any
    tenant_id: str
    actor_subject_id: str
    command: str
    correlation_id: str
    saved: Intervention | None = None
    linked: list[Intervention] = field(default_factory=list)

    def validate_linked(self, child: Intervention) -> None:
        if (
            self.command not in {"close", "adjust"}
            or self.linked
            or child.intervention_id == self.original.intervention_id
            or child.store_id != self.original.store_id
            or child.created_by != self.actor_subject_id
            or type(child.version) is not int
            or child.version != 1
            or child.status != InterventionStatus.CANDIDATE
            or child.history
        ):
            raise InterventionError("stale update: linked lifecycle creation context changed")
        if self.command == "adjust" and child.predecessor_id != self.original.intervention_id:
            raise InterventionError("stale update: replacement predecessor changed")
        if (
            self.command == "close"
            and child.trigger_ref != f"follow-up:{self.original.intervention_id}"
        ):
            raise InterventionError("stale update: follow-up context changed")

    def require_complete(self) -> None:
        if self.saved is None:
            raise InterventionError("stale update: lifecycle transition was not saved")
        ref = (
            self.saved.replacement_id
            if self.command == "adjust"
            else self.saved.close.follow_up_intervention_id
            if self.command == "close" and self.saved.close
            else None
        )
        if [c.intervention_id for c in self.linked] != ([ref] if ref else []):
            raise InterventionError("stale update: lifecycle linked pair incomplete")
