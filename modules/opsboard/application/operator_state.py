"""OperatorStateService — application-layer facade for operator console state.

This service mediates between the HTTP route sub-modules (shell, issues,
approvals, evidence, seed) and the in-memory R4 seed store.  It owns:

- get_today() → bootstrap / today payload
- get_work_queue() → current issues list
- get_approvals() → current approval decisions list
- transition_issue() → issue lifecycle write + audit
- decide_approval() → approval decision write + audit
- confirm_evidence_purpose() → evidence unlock write + audit
- reset_to_seed() → deterministic seed reset

Not changing: persistence layer, auth/RBAC, external DB adapters.
Composes with: operator_modules/* sub-routers via create_operator_router().
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from modules.opsboard.domain.r4_dtos import (
    ISSUE_STATUS_BY_ACTION,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    EvidencePurposeRequest,
    EvidencePurposeResponse,
    IssueTransitionRequest,
    IssueTransitionResponse,
)
from modules.opsboard.infrastructure.seed_data import load_r4_seed


class OperatorStateService:
    """In-memory operator console state service.

    Thread-safety: not guaranteed for concurrent writes in production.
    This service targets single-process FastAPI deployments with
    synchronous route handlers (no async_to_thread escalation needed
    for in-memory state).
    """

    def __init__(self) -> None:
        self._state: dict[str, Any] = load_r4_seed()
        self._idempotency_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get_today(self) -> dict[str, Any]:
        """Return a deep copy of the current bootstrap/today payload."""
        return copy.deepcopy(self._state)

    def get_work_queue(self) -> list[dict[str, Any]]:
        """Return a deep copy of the current work-queue items."""
        return copy.deepcopy(self._state.get("workQueue", []))

    def get_approvals(self) -> list[dict[str, Any]]:
        """Return a deep copy of the current approval decisions."""
        return copy.deepcopy(self._state.get("decisions", []))

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def transition_issue(
        self,
        *,
        issue_id: str,
        action_type: str,
        body: IssueTransitionRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> IssueTransitionResponse:
        """Transition an issue through its lifecycle, appending an audit event."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        new_status = ISSUE_STATUS_BY_ACTION.get(action_type, "closed")
        for item in self._state.get("workQueue", []):
            if item.get("id") == issue_id:
                item["status"] = new_status
                break

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "System",
                "category": "Workflow",
                "detail": (
                    f"Issue {issue_id} transitioned via '{action_type}' to '{new_status}'."
                    + (f" Note: {body.note}" if body.note else "")
                ),
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = IssueTransitionResponse(
            issueId=issue_id,
            newStatus=new_status,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def decide_approval(
        self,
        *,
        approval_id: str,
        body: ApprovalDecisionRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> ApprovalDecisionResponse:
        """Record an approval decision, appending an audit event."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        for dec in self._state.get("decisions", []):
            if dec.get("id") == approval_id:
                dec["status"] = body.status
                break

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "System",
                "category": "Decision log",
                "detail": (
                    f"Approval {approval_id} decided: {body.status}. Reason: {body.reason}"
                ),
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = ApprovalDecisionResponse(
            approvalId=approval_id,
            newStatus=body.status,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def confirm_evidence_purpose(
        self,
        *,
        evidence_id: str,
        body: EvidencePurposeRequest,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> EvidencePurposeResponse:
        """Unlock a locked evidence item by purpose declaration."""
        if idempotency_key and idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]  # type: ignore[return-value]

        audit_id = str(uuid.uuid4())
        self._state.setdefault("auditFeed", []).insert(
            0,
            {
                "actor": body.actorName or "Operator",
                "category": "Audit trail",
                "detail": f"Unlocked evidence {evidence_id} with purpose: {body.purpose}",
                "time": datetime.now(UTC).strftime("%H:%M"),
                "auditEventId": audit_id,
            },
        )

        response = EvidencePurposeResponse(
            evidenceId=evidence_id,
            purpose=body.purpose,
            auditEventId=audit_id,
            correlationId=correlation_id,
        )
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = response
        return response

    def reset_to_seed(self) -> None:
        """Deterministically reset state to the canonical R4 seed.

        Clears the idempotency cache as well so tests get a clean slate.
        """
        self._state = load_r4_seed()
        self._idempotency_cache = {}


__all__ = ["OperatorStateService"]
