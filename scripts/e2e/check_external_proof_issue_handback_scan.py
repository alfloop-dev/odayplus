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


def scan_issue_for_handbacks(
    *,
    task_id: str,
    issue: dict[str, Any],
    expected_sha: str,
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

    return {
        "task_id": task_id,
        "issue": f"#{issue.get('number')}",
        "issue_state": issue.get("state"),
        "status": status,
        "latest_pickup_created_at": latest_pickup_created_at,
        "candidate_handback_comments": candidate_comments,
    }


def scan_queue(queue: dict[str, Any], *, expected_sha: str, issues_by_number: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
                    "candidate_handback_comments": [],
                }
            )
            continue
        rows.append(scan_issue_for_handbacks(task_id=task_id, issue=issue, expected_sha=expected_sha))
    return rows


def render_markdown(rows: list[dict[str, Any]], *, expected_sha: str) -> str:
    lines = [
        "# External Proof Issue Handback Scan",
        "",
        f"PR #82 headRefOid: `{expected_sha}`",
        "",
        "| Task | Issue | Issue State | Latest Pickup | Status | Candidate Comments |",
        "|---|---|---|---|---|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['task_id']}` | {row['issue']} | {row['issue_state']} | "
            f"{row['latest_pickup_created_at'] or ''} | `{row['status']}` | "
            f"{len(row['candidate_handback_comments'])} |"
        )
    return "\n".join(lines) + "\n"


def validate_scan(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        if row["status"] in {"missing_current_sha_pickup", "missing_live_issue_payload"}:
            errors.append(f"{row['task_id']} {row['status']}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", help="expected PR #82 headRefOid; defaults to live gh pr view")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--report", action="store_true", help="emit Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha or current_pr82_head()
    queue = load_json(QUEUE_PATH)
    issue_numbers = [issue_number_from_url(str(entry["tracking_issue"])) for entry in queue.get("queue", [])]
    issues_by_number = {issue_number: load_issue(issue_number) for issue_number in issue_numbers}
    rows = scan_queue(queue, expected_sha=expected_sha, issues_by_number=issues_by_number)
    errors = validate_scan(rows)

    if args.json:
        print(json.dumps({"expected_sha": expected_sha, "rows": rows}, ensure_ascii=False, indent=2))
    elif args.report:
        print(render_markdown(rows, expected_sha=expected_sha), end="")
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
