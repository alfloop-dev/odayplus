#!/usr/bin/env python3
"""Verify PR #82 has a product closeout fleet update for the current head.

The product closeout pickup board is static repo evidence. The PR comment is
the live fleet-facing notification surface. This checker makes sure PR #82 has
a recent comment tied to the active `headRefOid` and containing every active
closeout queue action, so a release-candidate SHA change cannot leave owners,
reviewers, or Human/Ops following stale pickup instructions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"

REQUIRED_COMMENT_TOKENS = (
    "Product closeout fleet update",
    "Current release target: PR #82 headRefOid",
    "Ready lanes",
    "Waiting lanes",
    "Blocked or stale lanes",
    "check_product_closeout_action_matrix.py",
    "check_product_closeout_action.py",
    "Do not mark product release complete",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def current_pr82_head() -> str:
    raw = subprocess.check_output(
        ["gh", "pr", "view", "82", "--json", "headRefOid", "--jq", ".headRefOid"],
        cwd=ROOT,
        text=True,
    )
    return raw.strip()


def load_pr82_comments() -> dict[str, Any]:
    raw = subprocess.check_output(
        ["gh", "pr", "view", "82", "--json", "comments"],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def command_fragment(command: str) -> str:
    """Return a stable fragment for commands containing placeholders."""
    return command.split(" <")[0].split(' "<')[0]


def validate_notification(
    queue_payload: dict[str, Any],
    pr_payload: dict[str, Any],
    *,
    expected_sha: str,
) -> list[str]:
    errors: list[str] = []
    comments = pr_payload.get("comments", [])
    matching_comments = [
        str(comment.get("body", ""))
        for comment in comments
        if "Product closeout fleet update" in str(comment.get("body", ""))
        and expected_sha in str(comment.get("body", ""))
    ]
    if not matching_comments:
        return [f"PR #82 missing product closeout fleet update for headRefOid {expected_sha}"]

    latest_match = matching_comments[-1]
    for token in REQUIRED_COMMENT_TOKENS:
        if token not in latest_match:
            errors.append(f"latest product closeout fleet update missing token: {token}")

    for entry in queue_payload.get("queue", []):
        task_id = str(entry.get("task_id", ""))
        actor = str(entry.get("actor", ""))
        action_type = str(entry.get("action_type", ""))
        prefix = f"{task_id} / {actor} / {action_type}"
        for token in (task_id, actor, action_type):
            if token and token not in latest_match:
                errors.append(f"latest product closeout fleet update missing queue token for {prefix}: {token}")

        preflight = (
            f"check_product_closeout_action.py --task {task_id} --actor {actor} --action-type {action_type}"
        )
        if preflight not in latest_match:
            errors.append(f"latest product closeout fleet update missing preflight for {prefix}")

        for command in entry.get("allowed_commands", []):
            fragment = command_fragment(str(command))
            if fragment not in latest_match:
                errors.append(
                    f"latest product closeout fleet update missing allowed command fragment for {prefix}: {fragment}"
                )

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", help="expected PR #82 headRefOid; defaults to live gh pr view 82")
    parser.add_argument("--pr-json", type=Path, help="fixture PR comments JSON payload for deterministic tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha or current_pr82_head()
    queue_payload = load_json(QUEUE_PATH)
    pr_payload = load_json(args.pr_json) if args.pr_json else load_pr82_comments()

    errors = validate_notification(queue_payload, pr_payload, expected_sha=expected_sha)
    if errors:
        print("Product closeout fleet notification check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product closeout fleet notification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
