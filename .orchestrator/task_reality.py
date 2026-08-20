from __future__ import annotations

"""Compare a task record against the git and GitHub reality it points at.

The board carries fields that name things outside itself - `branch`,
`pr_number`, `merge_route`, `review_gate_sha`. Nothing has ever checked whether
those names still resolve, so when one drifts the task does not fail loudly; it
stops being dispatchable and waits for a person to notice. Between 2026-08-19
and 2026-08-20 four distinct drifts were each cleared by hand:

  * `merge_route` said `queued` while the dev merge queue was empty
  * `branch` named a branch deleted when its PR was closed as superseded
  * `pr_number` pointed at that closed PR rather than the one that replaced it
  * `review_gate_sha` was unset while the head carried no status check

This module answers one question - does the record still match reality - and
splits the answer in two.

**It repairs only what reality determines uniquely.** If a task's branch no
longer exists and its open PR names exactly one head ref, that ref is the
branch; there is nothing to decide. **Everything else it reports** onto the task
record and leaves alone.

That split is the whole design. A reconciler that guesses becomes a component
which can rewrite any field on any task with no reviewer, which is the shape of
the very problem it is here to fix: on 2026-08-19 a worker blocked by the done
gate edited the done gate. So this one is deliberately incapable of choosing
between two possible truths. When it cannot tell, it says so and stops.
"""

from typing import Any, Callable

MERGE_ROUTE_FIELD = "merge_route"

#: Statuses whose records still point at live work. A terminal task's fields are
#: history and must not be rewritten.
ACTIVE_TASK_STATUSES = frozenset(
    {"todo", "in_progress", "blocked", "review", "review_approved"}
)

#: Statuses that assert a reviewer has frozen a head, so a missing gate matters.
REVIEW_GATE_STATUSES = frozenset({"review", "review_approved"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def task_reality_findings(
    task: dict[str, Any],
    *,
    pull_request: dict[str, Any] | None,
    branch_exists: bool,
    head_has_review_gate: bool | None = None,
) -> list[dict[str, Any]]:
    """Describe every way ``task`` disagrees with what it names.

    ``pull_request`` is the record for ``task['pr_number']`` or ``None`` when
    there is no such PR. ``branch_exists`` is whether ``task['branch']``
    resolves on the remote. ``head_has_review_gate`` is whether the approved
    head carries the required review status, or ``None`` when not probed.

    Every finding carries ``repairable``. A repairable finding states the single
    value reality allows; the rest carry only a description.
    """

    findings: list[dict[str, Any]] = []
    status = _text(task.get("status")).lower()
    if status not in ACTIVE_TASK_STATUSES:
        return findings

    branch = _text(task.get("branch"))
    pr_state = _text((pull_request or {}).get("state")).upper()
    pr_merged = bool((pull_request or {}).get("merged"))
    pr_head_ref = _text((pull_request or {}).get("headRefName"))

    if branch and not branch_exists:
        # A branch that no longer resolves cannot be leased, and every dispatch
        # fails `unverifiable_refs` until someone corrects the record.
        if pr_state == "OPEN" and pr_head_ref and pr_head_ref != branch:
            findings.append(
                {
                    "kind": "branch_missing",
                    "repairable": True,
                    "field": "branch",
                    "value": pr_head_ref,
                    "detail": (
                        f"recorded branch `{branch}` does not exist on the remote; the open "
                        f"PR #{pull_request.get('number')} is on `{pr_head_ref}`"
                    ),
                }
            )
        else:
            findings.append(
                {
                    "kind": "branch_missing",
                    "repairable": False,
                    "field": "branch",
                    "detail": (
                        f"recorded branch `{branch}` does not exist on the remote and no open "
                        "PR names a replacement; an owner must point the task at real work"
                    ),
                }
            )

    if pull_request is not None and pr_state == "CLOSED" and not pr_merged:
        # Which PR replaced it is not derivable: a supersession may be recorded
        # in a comment, another branch, or nowhere at all. Say so; do not guess.
        findings.append(
            {
                "kind": "pr_closed_unmerged",
                "repairable": False,
                "field": "pr_number",
                "detail": (
                    f"recorded PR #{pull_request.get('number')} is closed without merging; an "
                    "owner must point the task at the PR that delivers it"
                ),
            }
        )

    if pr_merged and isinstance(task.get(MERGE_ROUTE_FIELD), dict):
        # The record described an attempt to get the PR merged. It merged.
        findings.append(
            {
                "kind": "merge_route_after_merge",
                "repairable": True,
                "field": MERGE_ROUTE_FIELD,
                "value": None,
                "detail": (
                    f"PR #{pull_request.get('number')} is merged, so the merge routing record "
                    "no longer describes anything"
                ),
            }
        )

    if (
        head_has_review_gate is False
        and status in REVIEW_GATE_STATUSES
        and _text(task.get("approved_head"))
    ):
        # Stamping a required check is a governance act. It is never repaired
        # here, however obvious the omission looks.
        findings.append(
            {
                "kind": "review_gate_absent",
                "repairable": False,
                "field": "review_gate_sha",
                "detail": (
                    "the reviewer-approved head carries no review gate status, so the required "
                    "check reads as absent; a reviewer must re-emit it"
                ),
            }
        )

    return findings


def apply_task_reality_repairs(
    task: dict[str, Any], findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply the repairable findings to ``task``; return the ones applied."""

    applied: list[dict[str, Any]] = []
    for finding in findings:
        if not finding.get("repairable"):
            continue
        field = _text(finding.get("field"))
        if not field:
            continue
        value = finding.get("value")
        if value is None:
            if task.pop(field, None) is None:
                continue
        else:
            if task.get(field) == value:
                continue
            task[field] = value
        applied.append(finding)
    return applied


def unrepairable_summary(task_id: str, findings: list[dict[str, Any]]) -> str:
    """One line an operator can act on, or an empty string when all was fixed."""

    outstanding = [f for f in findings if not f.get("repairable")]
    if not outstanding:
        return ""
    reasons = "; ".join(_text(f.get("detail")) for f in outstanding if _text(f.get("detail")))
    return (
        f"Task {task_id} names work that does not resolve: {reasons}. "
        "Dispatch cannot proceed until the record matches reality."
    )


def reconcile_tasks(
    tasks: list[dict[str, Any]],
    *,
    probe: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconcile every task, returning one result per task that had findings.

    ``probe`` supplies the reality for one task as a mapping with
    ``pull_request``, ``branch_exists`` and optionally ``head_has_review_gate``.
    Injecting it keeps this module free of network calls and testable against
    the exact drifts observed in production.
    """

    results: list[dict[str, Any]] = []
    for task in tasks:
        task_id = _text(task.get("id"))
        if not task_id:
            continue
        try:
            reality = probe(task)
        except Exception:
            # A probe that cannot answer is not evidence of drift. Leave the
            # record alone rather than "repairing" it from a failed lookup.
            continue
        findings = task_reality_findings(
            task,
            pull_request=reality.get("pull_request"),
            branch_exists=bool(reality.get("branch_exists")),
            head_has_review_gate=reality.get("head_has_review_gate"),
        )
        if not findings:
            continue
        applied = apply_task_reality_repairs(task, findings)
        results.append(
            {
                "task_id": task_id,
                "findings": findings,
                "applied": applied,
                "summary": unrepairable_summary(task_id, findings),
            }
        )
    return results
