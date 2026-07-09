#!/usr/bin/env python3
"""Scan #132-#138 for handback comments after the latest pickup update.

This is a live observability helper for Product Validation. It does not accept
or reject handbacks; it tells release owners whether fleets have submitted any
candidate handback material after the latest pickup comment for the current PR
#82 headRefOid.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"

HANDBACK_TOKENS = (
    "release_head_ref_oid",
    "handback_artifact",
    "external proof handback",
    "check_external_proof_handback_artifact.py",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def current_pr82_head() -> str:
    return subprocess.check_output(
        ["gh", "pr", "view", "82", "--json", "headRefOid", "--jq", ".headRefOid"],
        cwd=ROOT,
        text=True,
    ).strip()


def load_issue(issue_number: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        ["gh", "issue", "view", issue_number, "--json", "number,title,state,comments,url"],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def parse_github_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def pickup_age_hours(created_at: Any, *, now: datetime) -> float | None:
    created = parse_github_time(created_at)
    if created is None:
        return None
    return max((now.astimezone(UTC) - created).total_seconds() / 3600, 0.0)


def scan_issue_for_handbacks(
    *,
    task_id: str,
    issue: dict[str, Any],
    expected_sha: str,
    now: datetime,
    escalation_hours: float,
) -> dict[str, Any]:
    comments = issue.get("comments", [])
    latest_pickup_index = -1
    latest_pickup_created_at = None
    for index, comment in enumerate(comments):
        body = str(comment.get("body", ""))
        if (
            "External proof fleet pickup update" in body
            and task_id in body
            and expected_sha in body
        ):
            latest_pickup_index = index
            latest_pickup_created_at = comment.get("createdAt")

    candidate_comments: list[dict[str, Any]] = []
    if latest_pickup_index >= 0:
        for comment in comments[latest_pickup_index + 1 :]:
            body = str(comment.get("body", ""))
            lowered = body.lower()
            if task_id in body or any(token in lowered for token in HANDBACK_TOKENS):
                candidate_comments.append(
                    {
                        "author": comment.get("author", {}).get("login"),
                        "created_at": comment.get("createdAt"),
                        "contains_expected_sha": expected_sha in body,
                        "contains_artifact_checker": "check_external_proof_handback_artifact.py" in body,
                    }
                )

    status = "candidate_handback_detected" if candidate_comments else "no_handback_after_latest_pickup"
    if latest_pickup_index < 0:
        status = "missing_current_sha_pickup"
    age_hours = pickup_age_hours(latest_pickup_created_at, now=now)
    escalation_due = (
        status == "no_handback_after_latest_pickup"
        and age_hours is not None
        and age_hours >= escalation_hours
    )

    return {
        "task_id": task_id,
        "issue": f"#{issue.get('number')}",
        "issue_state": issue.get("state"),
        "status": status,
        "latest_pickup_created_at": latest_pickup_created_at,
        "pickup_age_hours": age_hours,
        "escalation_due": escalation_due,
        "candidate_handback_comments": candidate_comments,
    }


def scan_queue(
    queue: dict[str, Any],
    *,
    expected_sha: str,
    issues_by_number: dict[str, dict[str, Any]],
    now: datetime,
    escalation_hours: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in queue.get("queue", []):
        task_id = str(entry.get("task_id"))
        issue_number = issue_number_from_url(str(entry.get("tracking_issue", "")))
        issue = issues_by_number.get(issue_number)
        if not issue:
            rows.append(
                {
                    "task_id": task_id,
                    "issue": f"#{issue_number}",
                    "issue_state": "missing",
                    "status": "missing_live_issue_payload",
                    "latest_pickup_created_at": None,
                    "pickup_age_hours": None,
                    "escalation_due": False,
                    "candidate_handback_comments": [],
                }
            )
            continue
        rows.append(
            scan_issue_for_handbacks(
                task_id=task_id,
                issue=issue,
                expected_sha=expected_sha,
                now=now,
                escalation_hours=escalation_hours,
            )
        )
    return rows


def format_age(age: float | None) -> str:
    if age is None:
        return ""
    return f"{age:.1f}h"


def render_markdown(rows: list[dict[str, Any]], *, expected_sha: str, escalation_hours: float) -> str:
    lines = [
        "# External Proof Issue Handback Scan",
        "",
        f"PR #82 headRefOid: `{expected_sha}`",
        f"Escalation threshold: `{escalation_hours:g}h after latest pickup without handback`",
        "",
        "| Task | Issue | Issue State | Latest Pickup | Age | Status | Candidate Comments | Escalation Due |",
        "|---|---|---|---|---:|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['task_id']}` | {row['issue']} | {row['issue_state']} | "
            f"{row['latest_pickup_created_at'] or ''} | {format_age(row['pickup_age_hours'])} | "
            f"`{row['status']}` | {len(row['candidate_handback_comments'])} | "
            f"{'yes' if row['escalation_due'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def validate_scan(rows: list[dict[str, Any]], *, fail_on_escalation: bool) -> list[str]:
    errors: list[str] = []
    for row in rows:
        if row["status"] in {"missing_current_sha_pickup", "missing_live_issue_payload"}:
            errors.append(f"{row['task_id']} {row['status']}")
        if fail_on_escalation and row["escalation_due"]:
            errors.append(f"{row['task_id']} handback escalation due")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", help="expected PR #82 headRefOid; defaults to live gh pr view")
    parser.add_argument(
        "--escalation-hours",
        type=float,
        default=24.0,
        help="mark no-handback issues escalation_due after this many hours since latest pickup",
    )
    parser.add_argument(
        "--fail-on-escalation",
        action="store_true",
        help="return non-zero if any no-handback issue is past the escalation threshold",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--report", action="store_true", help="emit Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha or current_pr82_head()
    queue = load_json(QUEUE_PATH)
    issue_numbers = [issue_number_from_url(str(entry["tracking_issue"])) for entry in queue.get("queue", [])]
    issues_by_number = {issue_number: load_issue(issue_number) for issue_number in issue_numbers}
    now = datetime.now(UTC)
    rows = scan_queue(
        queue,
        expected_sha=expected_sha,
        issues_by_number=issues_by_number,
        now=now,
        escalation_hours=args.escalation_hours,
    )
    errors = validate_scan(rows, fail_on_escalation=args.fail_on_escalation)

    if args.json:
        print(json.dumps({"expected_sha": expected_sha, "rows": rows}, ensure_ascii=False, indent=2))
    elif args.report:
        print(render_markdown(rows, expected_sha=expected_sha, escalation_hours=args.escalation_hours), end="")
    elif errors:
        print("External proof issue handback scan failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("External proof issue handback scan checks passed.")
        pending = [row for row in rows if row["status"] == "no_handback_after_latest_pickup"]
        if pending:
            print(f"No handback after latest pickup: {len(pending)}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
