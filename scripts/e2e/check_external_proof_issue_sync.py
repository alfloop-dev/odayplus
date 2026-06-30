#!/usr/bin/env python3
"""Verify external-proof GitHub issues match the closeout queue routing.

This is an operator-facing live check, not a deterministic CI gate. It calls
`gh issue view` for each issue referenced by
`PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` and verifies the issue body and
labels still carry the fleet pickup routing required by the release packet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"


def load_queue() -> dict[str, Any]:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def load_issue(issue_number: str) -> dict[str, Any]:
    raw = subprocess.check_output(
        [
            "gh",
            "issue",
            "view",
            issue_number,
            "--json",
            "number,title,labels,assignees,body,url,state",
        ],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def normalize_label_names(issue: dict[str, Any]) -> set[str]:
    return {str(label.get("name")) for label in issue.get("labels", [])}


def validate_issue_sync(
    queue_payload: dict[str, Any],
    issues_by_number: dict[str, dict[str, Any]],
    *,
    require_assignees: bool = False,
) -> list[str]:
    errors: list[str] = []

    for entry in queue_payload.get("queue", []):
        task_id = str(entry.get("task_id"))
        issue_number = issue_number_from_url(str(entry.get("tracking_issue", "")))
        prefix = f"{task_id} issue #{issue_number}"
        issue = issues_by_number.get(issue_number)
        if not issue:
            errors.append(f"{prefix} missing live issue payload")
            continue

        if str(issue.get("state", "")).upper() != "OPEN":
            errors.append(f"{prefix} must stay open until external proof is accepted")

        routing = entry.get("fleet_routing", {})
        issue_body = str(issue.get("body", ""))
        issue_title = str(issue.get("title", ""))
        issue_labels = normalize_label_names(issue)
        required_labels = {str(label) for label in routing.get("required_issue_labels", [])}

        missing_labels = required_labels - issue_labels
        if missing_labels:
            errors.append(f"{prefix} missing labels: {sorted(missing_labels)}")

        expected_body_tokens = [
            "## Fleet pickup routing",
            "EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md",
            "## Runtime proof handback format",
            "EXTERNAL_PROOF_HANDBACK_TEMPLATE.json",
            "EXTERNAL_PROOF_HANDBACK_EXAMPLE.json",
            "generate_external_proof_handback_skeleton.py",
            "check_external_proof_handback_template.py",
            "check_external_proof_handback_artifact.py",
            "check_external_proof_handback_bundle.py",
            "#132-#138",
            "--expected-sha",
            str(routing.get("dispatch_lane", "")),
            str(routing.get("pickup_label", "")),
            str(routing.get("pickup_command", "")),
            str(routing.get("release_authority", "")),
            str(routing.get("escalation", "")),
            "## Completion rule",
            "Do not close",
        ]
        for token in expected_body_tokens:
            if token and token not in issue_body:
                errors.append(f"{prefix} body missing token: {token}")

        title = str(entry.get("title", ""))
        if title and title not in issue_title:
            errors.append(f"{prefix} title missing queue title: {title}")

        for token in (
            f"Task: `{task_id}`",
            f"Owner: `{entry.get('owner')}`",
            f"Reviewer: `{entry.get('reviewer')}`",
            f"Blocking type: `{entry.get('blocking_type')}`",
            "## Required evidence",
            "## Allowed commands",
            "## Evidence refs",
        ):
            if token not in issue_body:
                errors.append(f"{prefix} body missing queue-derived token: {token}")

        for evidence in entry.get("required_evidence", []):
            evidence_token = f"- [ ] {evidence}"
            if evidence_token not in issue_body:
                errors.append(f"{prefix} body missing required evidence: {evidence}")

        for command in entry.get("allowed_commands", []):
            if str(command) not in issue_body:
                errors.append(f"{prefix} body missing allowed command: {command}")

        for evidence_ref in entry.get("evidence_refs", []):
            evidence_ref_token = f"`{evidence_ref}`"
            if evidence_ref_token not in issue_body:
                errors.append(f"{prefix} body missing evidence ref: {evidence_ref}")

        completion_rule = str(entry.get("completion_rule", ""))
        if completion_rule and completion_rule not in issue_body:
            errors.append(f"{prefix} body missing completion rule: {completion_rule}")

        for label in required_labels:
            label_token = f"`{label}`"
            if label_token not in issue_body:
                errors.append(f"{prefix} body missing required label token: {label_token}")

        if require_assignees and not issue.get("assignees"):
            errors.append(f"{prefix} has no assignee")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-assignees",
        action="store_true",
        help="also fail if any external-proof issue has no GitHub assignee",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_payload = load_queue()
    issues_by_number = {
        issue_number_from_url(str(entry["tracking_issue"])): load_issue(
            issue_number_from_url(str(entry["tracking_issue"]))
        )
        for entry in queue_payload.get("queue", [])
    }
    errors = validate_issue_sync(
        queue_payload,
        issues_by_number,
        require_assignees=args.require_assignees,
    )
    if errors:
        print("External proof issue sync failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof issue sync checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
