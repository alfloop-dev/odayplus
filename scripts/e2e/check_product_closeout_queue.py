#!/usr/bin/env python3
"""Validate the product release closeout queue.

The queue is intentionally usable in two environments:

- CI/release-gate worktrees, where `ai-status.json` may not be present. In that
  mode this script validates queue schema, evidence refs, commands, boundaries,
  and coverage of required remaining tasks.
- Fleet/operator worktrees, where `ai-status.json` exists. In that mode it also
  checks that queued task status and actor fields still match the live task
  board, so stale closeout instructions are caught before humans act on them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
STATUS_PATH = ROOT / "ai-status.json"

REQUIRED_TASK_IDS = {
    "ODP-PV-008",
    "ODP-FE-XCUT-001",
    "ODP-FE-R0-001",
    "ODP-FE-XCUT-UI-001",
    "ODP-FE-EXP-001",
    "ODP-FE-OPS-001",
    "ODP-FE-PRICE-001",
    "ODP-FE-ASSET-001",
    "ODP-FE-LEARN-001",
    "ODP-FE-XCUT-DOMAIN-001",
    "ODP-FE-XCUT-TYPES-001",
}

REQUIRED_BLOCKING_TYPES = {
    "human_signoff",
    "owner_status_closeout",
    "reviewer_status_closeout",
}

REQUIRED_BOUNDARY_TOKENS = (
    "provider credential/OAuth wiring",
    "scheduled external fetch",
    "quota/rate-limit handling",
    "live tile rollout",
    "full keyboard accessibility",
    "remote staging host/url/secret configuration",
)

AI_STATUS_ACTOR_BY_ACTION = {
    "go_no_go": "reviewer",
    "owner_handoff": "owner",
    "owner_done": "owner",
    "reviewer_approve_or_reopen": "reviewer",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def status_task_index() -> dict[str, dict[str, Any]]:
    if not STATUS_PATH.exists():
        return {}
    payload = load_json(STATUS_PATH)
    return {str(task.get("id")): task for task in payload.get("tasks", [])}


def expected_actor_from_status(task: dict[str, Any], action_type: str) -> str | None:
    field = AI_STATUS_ACTOR_BY_ACTION.get(action_type)
    if field is None:
        return None
    value = task.get(field)
    return str(value) if value else None


def validate_queue(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    release_target = payload.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("release_target.pr must be 82")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("release_target.must_not_hardcode_dev_hash must be true")

    preflight = payload.get("global_preflight", [])
    if "gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url" not in preflight:
        errors.append("global_preflight must verify PR #82 head/checks")
    if "python3 scripts/e2e/check_product_release_gate.py" not in preflight:
        errors.append("global_preflight must run the product release gate")

    text = json.dumps(payload, ensure_ascii=False)
    for token in REQUIRED_BOUNDARY_TOKENS:
        if token not in text:
            errors.append(f"queue boundary does not mention {token!r}")

    entries = payload.get("queue", [])
    if not isinstance(entries, list) or not entries:
        return [*errors, "queue must be a non-empty list"]

    task_ids = {str(entry.get("task_id")) for entry in entries}
    missing_tasks = REQUIRED_TASK_IDS - task_ids
    if missing_tasks:
        errors.append(f"queue is missing tasks: {sorted(missing_tasks)}")

    blocking_types = {str(entry.get("blocking_type")) for entry in entries}
    missing_blocking_types = REQUIRED_BLOCKING_TYPES - blocking_types
    if missing_blocking_types:
        errors.append(f"queue is missing blocking types: {sorted(missing_blocking_types)}")

    status_index = status_task_index()
    for index, entry in enumerate(entries):
        prefix = f"queue[{index}] {entry.get('task_id')}"
        for required_field in (
            "task_id",
            "status",
            "actor",
            "action_type",
            "allowed_commands",
            "evidence_refs",
            "blocking_type",
        ):
            if not entry.get(required_field):
                errors.append(f"{prefix} missing {required_field}")

        for evidence_ref in entry.get("evidence_refs", []):
            evidence_path = ROOT / str(evidence_ref)
            if not evidence_path.exists():
                errors.append(f"{prefix} evidence ref missing: {evidence_ref}")

        task_id = str(entry.get("task_id"))
        status_task = status_index.get(task_id)
        if not status_task:
            continue
        queue_status = str(entry.get("status"))
        live_status = str(status_task.get("status"))
        if queue_status.startswith("waiting_for_"):
            if task_id == "ODP-FE-XCUT-001" and live_status not in {"in_progress", "review"}:
                errors.append(f"{prefix} waiting entry no longer matches live status {live_status}")
        elif queue_status != live_status:
            errors.append(f"{prefix} status {queue_status} does not match ai-status {live_status}")

        expected_actor = expected_actor_from_status(status_task, str(entry.get("action_type")))
        if expected_actor and str(entry.get("actor")) != expected_actor:
            errors.append(f"{prefix} actor {entry.get('actor')} does not match ai-status {expected_actor}")

    return errors


def main() -> int:
    payload = load_json(QUEUE_PATH)
    errors = validate_queue(payload)
    if errors:
        print("Product closeout queue check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Product closeout queue checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
