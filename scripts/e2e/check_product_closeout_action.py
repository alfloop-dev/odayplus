#!/usr/bin/env python3
"""Preflight a single product closeout lifecycle action.

The closeout queue and pickup board say who should do which action. This script
checks one intended action before an owner/reviewer/Human-Ops command is run:
queue routing, live ai-status state, evidence refs, allowed command text, and
PR #82 attached checks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
DEFAULT_STATUS_ROOT = (
    Path(os.path.expanduser(os.environ["PANTHEON_STATUS_ROOT"])).resolve()
    if os.environ.get("PANTHEON_STATUS_ROOT")
    else ROOT
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

AI_STATUS_ACTOR_BY_ACTION = {
    "go_no_go": "reviewer",
    "owner_handoff": "owner",
    "owner_done": "owner",
    "reviewer_approve_or_reopen": "reviewer",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def issue_action_state(queue_status: str, live_status: str, action_type: str) -> str:
    if action_type == "owner_done":
        return "ready" if live_status == "review_approved" else "not_ready"
    if action_type == "owner_handoff":
        return "ready" if live_status == "in_progress" else "not_ready"
    if action_type == "reviewer_approve_or_reopen":
        return "ready" if live_status == "review" else "not_ready"
    if action_type == "go_no_go":
        return "ready" if live_status == "review" else "not_ready"
    if queue_status == live_status:
        return "ready"
    return "not_ready"


def load_pr82_payload(path: Path | None, *, skip_live: bool) -> dict[str, Any] | None:
    if path:
        return load_json(path)
    if skip_live:
        return None
    raw = subprocess.check_output(
        [
            "gh",
            "pr",
            "view",
            "82",
            "--json",
            "number,state,isDraft,headRefOid,mergeStateStatus,statusCheckRollup,url",
        ],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def validate_pr82(payload: dict[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    errors: list[str] = []
    if payload.get("number") != 82:
        errors.append("PR payload must describe PR #82")
    if payload.get("state") != "OPEN":
        errors.append("PR #82 must be open")
    head = payload.get("headRefOid")
    if not isinstance(head, str) or not SHA_RE.match(head):
        errors.append("PR #82 headRefOid must be a 40-character git SHA")
    if payload.get("mergeStateStatus") != "CLEAN":
        errors.append(f"PR #82 mergeStateStatus must be CLEAN, got {payload.get('mergeStateStatus')!r}")

    checks = payload.get("statusCheckRollup")
    if not isinstance(checks, list) or not checks:
        errors.append("PR #82 must have attached status checks")
        return errors
    for check in checks:
        name = str(check.get("name", "<unnamed>"))
        if check.get("status") != "COMPLETED" or check.get("conclusion") != "SUCCESS":
            errors.append(f"PR #82 check {name!r} must be COMPLETED/SUCCESS")
    return errors


def status_index(status_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task.get("id")): task for task in status_payload.get("tasks", [])}


def matching_queue_entries(
    queue_payload: dict[str, Any],
    *,
    task_id: str,
    actor: str,
    action_type: str,
) -> list[dict[str, Any]]:
    return [
        entry
        for entry in queue_payload.get("queue", [])
        if entry.get("task_id") == task_id
        and entry.get("actor") == actor
        and entry.get("action_type") == action_type
    ]


def validate_closeout_action(
    queue_payload: dict[str, Any],
    status_payload: dict[str, Any],
    *,
    task_id: str,
    actor: str,
    action_type: str,
    pr_payload: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    entries = matching_queue_entries(
        queue_payload,
        task_id=task_id,
        actor=actor,
        action_type=action_type,
    )
    if not entries:
        return [f"no closeout queue entry for task={task_id} actor={actor} action_type={action_type}"]
    if len(entries) > 1:
        errors.append(f"multiple closeout queue entries matched task={task_id} actor={actor} action_type={action_type}")
    entry = entries[0]

    live_task = status_index(status_payload).get(task_id)
    if live_task is None:
        errors.append(f"{task_id} is missing from ai-status.json")
    else:
        live_status = str(live_task.get("status", ""))
        ready_state = issue_action_state(str(entry.get("status", "")), live_status, action_type)
        if ready_state != "ready":
            errors.append(
                f"{task_id} action {action_type} is not ready: "
                f"queue status {entry.get('status')!r}, live status {live_status!r}"
            )
        actor_field = AI_STATUS_ACTOR_BY_ACTION.get(action_type)
        expected_actor = str(live_task.get(actor_field, "")) if actor_field else ""
        if expected_actor and expected_actor != actor:
            errors.append(f"{task_id} actor {actor!r} does not match ai-status {actor_field} {expected_actor!r}")

    for evidence_ref in entry.get("evidence_refs", []):
        evidence_path = ROOT / str(evidence_ref)
        if not evidence_path.exists():
            errors.append(f"{task_id} evidence ref is missing: {evidence_ref}")

    allowed_commands = entry.get("allowed_commands")
    if not isinstance(allowed_commands, list) or not allowed_commands:
        errors.append(f"{task_id} closeout entry must define allowed_commands")
    else:
        allowed_text = "\n".join(str(command) for command in allowed_commands)
        if action_type == "go_no_go" and "check_product_release_gate.py" not in allowed_text:
            errors.append(f"{task_id} go/no-go action must run check_product_release_gate.py")
        if action_type != "go_no_go" and "scripts/ai_status.py" not in allowed_text:
            errors.append(f"{task_id} closeout action must use scripts/ai_status.py")

    errors.extend(validate_pr82(pr_payload))
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Closeout task id, e.g. ODP-FE-EXP-001")
    parser.add_argument("--actor", required=True, help="Expected closeout actor, e.g. Claude or Codex")
    parser.add_argument("--action-type", required=True, help="Queue action type to preflight")
    parser.add_argument("--queue", type=Path, default=QUEUE_PATH, help="Closeout queue JSON")
    parser.add_argument(
        "--status-path",
        type=Path,
        default=DEFAULT_STATUS_ROOT / "ai-status.json",
        help="ai-status.json path; defaults to PANTHEON_STATUS_ROOT/ai-status.json or repo ai-status.json",
    )
    parser.add_argument("--pr-json", type=Path, help="Fixture PR #82 JSON payload for deterministic tests")
    parser.add_argument("--skip-pr-check", action="store_true", help="skip live PR #82 validation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_payload = load_json(args.queue)
    status_payload = load_json(args.status_path)
    pr_payload = load_pr82_payload(args.pr_json, skip_live=args.skip_pr_check)
    errors = validate_closeout_action(
        queue_payload,
        status_payload,
        task_id=args.task,
        actor=args.actor,
        action_type=args.action_type,
        pr_payload=pr_payload,
    )
    if errors:
        print("Product closeout action preflight failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Product closeout action preflight passed "
        f"for {args.task} actor={args.actor} action_type={args.action_type}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
