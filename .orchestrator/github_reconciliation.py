from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ai_status as runtime_ai_status


@dataclass(frozen=True)
class FinalizeGateResult:
    status: str
    current_head: str | None = None
    approved_head: str | None = None
    pr_status: str | None = None
    ci_status: str | None = None
    error: str | None = None


READY = "ready"
MISSING_APPROVED_HEAD = "missing_approved_head"
HEAD_MISMATCH = "head_mismatch"
HEAD_UNRESOLVED = "head_unresolved"
CI_PENDING = "ci_pending"
CI_FAILURE = "ci_failure"
CI_UNRESOLVED = "ci_unresolved"
PR_NOT_MERGED = "pr_not_merged"


def evaluate_finalize_gate(task: dict[str, Any]) -> FinalizeGateResult:
    """Evaluate whether a review_approved task is ready for finalize dispatch.

    The check intentionally mirrors the previous in-supervisor logic in
    `dispatch_ready_tasks` and `dispatch_priority_for_task` so both paths share
    one source of truth and do not diverge.
    """

    task_id = str(task.get("id") or "")
    approved_head = task.get("approved_head")
    if not approved_head:
        return FinalizeGateResult(status=MISSING_APPROVED_HEAD, approved_head=None)

    try:
        current_head = runtime_ai_status.resolve_task_checkout_sha(task, force_refresh=True)
    except Exception as exc:
        return FinalizeGateResult(
            status=HEAD_UNRESOLVED,
            approved_head=str(approved_head),
            error=f"{type(exc).__name__}: {exc}",
        )

    if current_head is not None:
        try:
            current_head = str(current_head).strip()
        except Exception:
            current_head = None

    if not current_head:
        return FinalizeGateResult(
            status=HEAD_UNRESOLVED,
            approved_head=str(approved_head),
            error="Unable to resolve current task HEAD.",
        )

    if not runtime_ai_status.is_approved_head_satisfied(task, current_head, approved_head):
        return FinalizeGateResult(
            status=HEAD_MISMATCH,
            current_head=current_head,
            approved_head=str(approved_head),
        )

    try:
        pr_status, ci_status = runtime_ai_status.task_pr_ci_status(task_id)
    except Exception as exc:
        return FinalizeGateResult(
            status=CI_UNRESOLVED,
            current_head=current_head,
            approved_head=str(approved_head),
            error=f"{type(exc).__name__}: {exc}",
        )

    pr_status = str(pr_status or "").strip().upper()
    ci_status = str(ci_status or "").strip().lower()
    if ci_status == "pending":
        return FinalizeGateResult(
            status=CI_PENDING,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )
    if ci_status == "failure":
        return FinalizeGateResult(
            status=CI_FAILURE,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )
    if ci_status not in {"success", "none"}:
        return FinalizeGateResult(
            status=CI_UNRESOLVED,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )

    if pr_status != "MERGED":
        return FinalizeGateResult(
            status=PR_NOT_MERGED,
            current_head=current_head,
            approved_head=str(approved_head),
            pr_status=pr_status,
            ci_status=ci_status,
        )

    return FinalizeGateResult(
        status=READY,
        current_head=current_head,
        approved_head=str(approved_head),
        pr_status=pr_status,
        ci_status=ci_status,
    )
