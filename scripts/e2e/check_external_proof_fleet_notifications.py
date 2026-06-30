#!/usr/bin/env python3
"""Verify #132-#138 have fleet pickup comments for the current release head.

The queue and issue bodies tell fleets what to do. This live checker verifies
that each external-proof blocker also has a recent pickup comment tied to the
current PR #82 `headRefOid`, so a new release-candidate commit cannot silently
leave fleet instructions pointing at an older SHA.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"

REQUIRED_COMMENT_TOKENS = (
    "External proof fleet pickup update",
    "Current release target: PR #82 headRefOid",
    "Required runtime evidence",
    "Minimum commands/proof to attach",
    "Handback flow",
    "generate_external_proof_handback_skeleton.py",
    "check_external_proof_handback_artifact.py",
    "check_external_proof_live_blockers.py --require-assignees",
    "Do not close this issue",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def current_pr82_head() -> str:
    raw = subprocess.check_output(
        ["gh", "pr", "view", "82", "--json", "headRefOid", "--jq", ".headRefOid"],
        cwd=ROOT,
        text=True,
    )
    return raw.strip()


def load_issue(issue_number: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "gh",
            "issue",
            "view",
            issue_number,
            "--json",
            "number,title,state,comments,url",
        ],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def validate_notifications(
    queue_payload: dict[str, Any],
    issues_by_number: dict[str, dict[str, Any]],
    *,
    expected_sha: str,
) -> list[str]:
    errors: list[str] = []

    for entry in queue_payload.get("queue", []):
        task_id = str(entry.get("task_id"))
        issue_number = issue_number_from_url(str(entry.get("tracking_issue", "")))
        issue = issues_by_number.get(issue_number)
        prefix = f"{task_id} issue #{issue_number}"
        if not issue:
            errors.append(f"{prefix} missing live issue payload")
            continue

        comments = issue.get("comments", [])
        matching_comments = [
            str(comment.get("body", ""))
            for comment in comments
            if task_id in str(comment.get("body", "")) and expected_sha in str(comment.get("body", ""))
        ]
        if not matching_comments:
            errors.append(f"{prefix} missing fleet pickup comment for PR #82 headRefOid {expected_sha}")
            continue

        latest_match = matching_comments[-1]
        for token in REQUIRED_COMMENT_TOKENS:
            if token not in latest_match:
                errors.append(f"{prefix} latest pickup comment missing token: {token}")

        for evidence in entry.get("required_evidence", []):
            if str(evidence) not in latest_match:
                errors.append(f"{prefix} latest pickup comment missing required evidence: {evidence}")

        for command in entry.get("allowed_commands", []):
            command = str(command)
            stable_fragment = command.split(" <")[0].split(' "<')[0]
            if stable_fragment not in latest_match:
                errors.append(f"{prefix} latest pickup comment missing command fragment: {stable_fragment}")

        for command in entry.get("handback_commands", []):
            command = str(command)
            stable_fragment = command.split(" <")[0].split(' "<')[0]
            if stable_fragment not in latest_match:
                errors.append(f"{prefix} latest pickup comment missing handback command fragment: {stable_fragment}")

        completion_rule = str(entry.get("completion_rule", ""))
        if completion_rule and completion_rule not in latest_match:
            errors.append(f"{prefix} latest pickup comment missing completion rule")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-sha",
        help="expected PR #82 headRefOid; defaults to live gh pr view 82",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha or current_pr82_head()
    queue_payload = load_json(QUEUE_PATH)
    issue_numbers = [
        issue_number_from_url(str(entry["tracking_issue"]))
        for entry in queue_payload.get("queue", [])
    ]
    issues_by_number = {issue_number: load_issue(issue_number) for issue_number in issue_numbers}

    errors = validate_notifications(queue_payload, issues_by_number, expected_sha=expected_sha)
    if errors:
        print("External proof fleet notification check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof fleet notification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
