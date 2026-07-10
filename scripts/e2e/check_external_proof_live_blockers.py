#!/usr/bin/env python3
"""Verify live external-proof release blockers match handback acceptance state.

This is an operator-facing live check. It calls `gh issue view` for each
#132-#138 issue referenced by the external-proof queue, then compares the live
GitHub issue state to `EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`.

Rule: a tracking issue may be closed only after the matching handback status is
`accepted`. Pending/submitted/needs-revision tasks must stay open, labeled, and
routed until Product Validation accepts the handback artifact.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
STATUS_BOARD_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"

ACTIVE_HANDOFF_STATUSES = {
    "pending_external_handback",
    "handback_submitted",
    "needs_revision",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def normalize_label_names(issue: dict[str, Any]) -> set[str]:
    return {str(label.get("name")) for label in issue.get("labels", [])}


def load_issue(issue_number: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "gh",
            "issue",
            "view",
            issue_number,
            "--json",
            "number,title,labels,assignees,url,state",
        ],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def validate_live_blockers(
    queue_payload: dict[str, Any],
    status_board: dict[str, Any],
    issues_by_number: dict[str, dict[str, Any]],
    *,
    require_assignees: bool = False,
) -> list[str]:
    errors: list[str] = []

    queue_entries = {str(entry.get("task_id")): entry for entry in queue_payload.get("queue", [])}
    status_entries = {str(entry.get("task_id")): entry for entry in status_board.get("tasks", [])}

    if set(queue_entries) != set(status_entries):
        errors.append(
            "external proof live blocker task ids must match queue/status board: "
            f"missing_status={sorted(set(queue_entries) - set(status_entries))}, "
            f"extra_status={sorted(set(status_entries) - set(queue_entries))}"
        )

    for task_id, queue_entry in sorted(queue_entries.items()):
        status_entry = status_entries.get(task_id, {})
        issue_number = issue_number_from_url(str(queue_entry.get("tracking_issue", "")))
        issue = issues_by_number.get(issue_number)
        prefix = f"{task_id} issue #{issue_number}"
        if not issue:
            errors.append(f"{prefix} missing live issue payload")
            continue

        handback_status = str(status_entry.get("status", ""))
        issue_state = str(issue.get("state", "")).upper()
        labels = normalize_label_names(issue)
        routing = queue_entry.get("fleet_routing", {})
        required_labels = {str(label) for label in routing.get("required_issue_labels", [])}

        if handback_status in ACTIVE_HANDOFF_STATUSES:
            if issue_state != "OPEN":
                errors.append(
                    f"{prefix} is {issue_state or 'UNKNOWN'} but handback status is {handback_status}; "
                    "release blockers must stay open until accepted"
                )
            missing_labels = required_labels - labels
            if missing_labels:
                errors.append(f"{prefix} missing active blocker labels: {sorted(missing_labels)}")
            if require_assignees and not issue.get("assignees"):
                errors.append(f"{prefix} has no assignee while handback is {handback_status}")

        if handback_status == "accepted":
            if status_entry.get("artifact_check_passed") is not True:
                errors.append(f"{task_id} accepted handback must have artifact_check_passed=true")
            for field in ("accepted_release_head_ref_oid", "accepted_at", "accepted_by"):
                if not status_entry.get(field):
                    errors.append(f"{task_id} accepted handback missing {field}")

        if handback_status != "accepted" and issue_state == "CLOSED":
            errors.append(f"{prefix} cannot be closed before Product Validation accepts the handback")

        title = str(queue_entry.get("title", ""))
        issue_title = str(issue.get("title", ""))
        if title and title not in issue_title:
            errors.append(f"{prefix} title missing queue title: {title}")

    bundle = status_board.get("bundle_status", {})
    if str(bundle.get("status", "")) == "accepted":
        unaccepted = [
            task_id
            for task_id, entry in sorted(status_entries.items())
            if str(entry.get("status", "")) != "accepted"
        ]
        if unaccepted:
            errors.append(f"bundle_status is accepted but tasks are not accepted: {unaccepted}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-assignees",
        action="store_true",
        help="also fail if any active external-proof blocker has no GitHub assignee",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_payload = load_json(QUEUE_PATH)
    status_board = load_json(STATUS_BOARD_PATH)
    issue_numbers = [
        issue_number_from_url(str(entry["tracking_issue"]))
        for entry in queue_payload.get("queue", [])
    ]
    issues_by_number = {issue_number: load_issue(issue_number) for issue_number in issue_numbers}

    errors = validate_live_blockers(
        queue_payload,
        status_board,
        issues_by_number,
        require_assignees=args.require_assignees,
    )
    if errors:
        print("External proof live blocker check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof live blocker checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
