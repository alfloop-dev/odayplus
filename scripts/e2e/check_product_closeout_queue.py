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

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
MANIFEST_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md"
STATUS_ROOT = (
    Path(os.path.expanduser(os.environ["PANTHEON_STATUS_ROOT"])).resolve()
    if os.environ.get("PANTHEON_STATUS_ROOT")
    else ROOT
)
STATUS_PATH = STATUS_ROOT / "ai-status.json"

REQUIRED_ACTIVE_TASK_IDS = {
    "ODP-PV-008",
    "ODP-FE-XCUT-001",
    "ODP-FE-R0-001",
    "ODP-FE-EXP-001",
    "ODP-FE-ASSET-001",
    "ODP-FE-XCUT-DOMAIN-001",
}

REQUIRED_COMPLETED_TASK_IDS = {
    "ODP-FE-XCUT-UI-001",
    "ODP-FE-OPS-001",
    "ODP-FE-PRICE-001",
    "ODP-FE-LEARN-001",
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


def live_entry_state(entry: dict[str, Any], status_task: dict[str, Any] | None) -> str:
    """Return a report-only lifecycle state for a closeout queue entry."""
    if not status_task:
        return "queued_no_live_status"

    queue_status = str(entry.get("status"))
    live_status = str(status_task.get("status"))
    action_type = str(entry.get("action_type"))

    if action_type == "owner_done" and live_status == "done":
        return "completed"
    if action_type == "reviewer_approve_or_reopen" and live_status in {"review_approved", "done"}:
        return "completed"
    if action_type == "owner_handoff" and live_status in {"review", "review_approved", "done"}:
        return "completed"
    if action_type == "go_no_go" and live_status in {"review_approved", "done"}:
        return "completed"

    if queue_status.startswith("waiting_for_"):
        if live_status == "review":
            return "active"
        if live_status == "in_progress":
            return "waiting_for_handoff"
        return "stale"

    if queue_status == live_status:
        return "active"
    return "stale"


def render_report(payload: dict[str, Any], errors: list[str]) -> str:
    status_index = status_task_index()
    has_live_status = bool(status_index)
    release_target = payload.get("release_target", {})
    entries = payload.get("queue", [])
    completed_entries = payload.get("completed_closeouts", [])

    rows: list[dict[str, str]] = []
    for entry in entries:
        task_id = str(entry.get("task_id"))
        live_task = status_index.get(task_id)
        rows.append(
            {
                "task_id": task_id,
                "queue_status": str(entry.get("status")),
                "live_status": str(live_task.get("status")) if live_task else "not_loaded",
                "actor": str(entry.get("actor")),
                "action_type": str(entry.get("action_type")),
                "blocking_type": str(entry.get("blocking_type")),
                "state": live_entry_state(entry, live_task),
            }
        )

    actor_counts = Counter(row["actor"] for row in rows if row["state"] != "completed")
    blocking_counts = Counter(row["blocking_type"] for row in rows if row["state"] != "completed")

    lines = [
        "# Product Release Closeout Queue Report",
        "",
        "## Release Target",
        "",
        f"- PR: #{release_target.get('pr')}",
        f"- Authority: {release_target.get('authority')}",
        f"- Must not hardcode dev hash: {release_target.get('must_not_hardcode_dev_hash')}",
        "",
        "## Validation",
        "",
        f"- ai-status.json loaded: {str(has_live_status).lower()}",
        f"- Queue validation: {'failed' if errors else 'passed'}",
    ]
    if errors:
        lines.extend(["", "### Validation Errors", ""])
        lines.extend(f"- {error}" for error in errors)

    lines.extend(
        [
            "",
            "## Active Counts By Actor",
            "",
        ]
    )
    if actor_counts:
        lines.extend(f"- {actor}: {count}" for actor, count in sorted(actor_counts.items()))
    else:
        lines.append("- none")

    lines.extend(["", "## Active Counts By Blocking Type", ""])
    if blocking_counts:
        lines.extend(f"- {blocking_type}: {count}" for blocking_type, count in sorted(blocking_counts.items()))
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Queue Entries",
            "",
            "| Task | Queue Status | Live Status | Actor | Action | Blocking Type | State |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {task_id} | {queue_status} | {live_status} | {actor} | {action_type} | "
            "{blocking_type} | {state} |".format(**row)
        )

    if completed_entries:
        lines.extend(
            [
                "",
                "## Completed Closeouts",
                "",
                "| Task | Status | Evidence |",
                "|---|---|---|",
            ]
        )
        for entry in completed_entries:
            evidence = ", ".join(str(ref) for ref in entry.get("evidence_refs", []))
            lines.append(f"| {entry.get('task_id')} | {entry.get('status')} | {evidence} |")

    lines.extend(["", "## Scope Boundaries", ""])
    for boundary in payload.get("scope_boundaries", []):
        lines.append(f"### {boundary.get('topic')}")
        lines.append("")
        lines.append(f"- Current proof: {boundary.get('current_proof')}")
        lines.append("- Not proven:")
        for item in boundary.get("not_proven", []):
            lines.append(f"  - {item}")
        lines.append("")

    lines.extend(
        [
            "## Operator Notes",
            "",
            "- External data source proof includes deterministic fixtures/source-stub coverage, live-provider "
            "adapter tests, scheduled external fetch worker proof, quota/rate-limit handling, freshness "
            "gates, licensing gates, and product E2E mock proof. Provider-specific production credential "
            "rotation and licensing approval remain outside this proof.",
            "- Map proof includes deterministic local MapLibre/deck/H3 E2E, live tile/geocoder boundary "
            "checks, full keyboard accessibility, layer toggles, direct map picking, semantic pixel checks, "
            "resilience states, and tooltip/evidence detail. Remote-staging live tile/geocoder rollout "
            "remains conditional.",
            "- Remote staging rollout remains conditional until host, URL, secret owner configuration, "
            "health/version smoke, and staging drill evidence are present.",
        ]
    )

    return "\n".join(lines) + "\n"


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
    missing_tasks = REQUIRED_ACTIVE_TASK_IDS - task_ids
    if missing_tasks:
        errors.append(f"queue is missing active tasks: {sorted(missing_tasks)}")

    completed_task_ids = {str(entry.get("task_id")) for entry in payload.get("completed_closeouts", [])}
    missing_completed_tasks = REQUIRED_COMPLETED_TASK_IDS - completed_task_ids
    if missing_completed_tasks:
        errors.append(f"completed_closeouts is missing tasks: {sorted(missing_completed_tasks)}")
    overlap = task_ids & completed_task_ids
    if overlap:
        errors.append(f"tasks cannot be both active and completed: {sorted(overlap)}")

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

    for index, entry in enumerate(payload.get("completed_closeouts", [])):
        prefix = f"completed_closeouts[{index}] {entry.get('task_id')}"
        for required_field in ("task_id", "status", "evidence_refs", "completion_note"):
            if not entry.get(required_field):
                errors.append(f"{prefix} missing {required_field}")
        if entry.get("status") != "done":
            errors.append(f"{prefix} status must be done")
        for evidence_ref in entry.get("evidence_refs", []):
            evidence_path = ROOT / str(evidence_ref)
            if not evidence_path.exists():
                errors.append(f"{prefix} evidence ref missing: {evidence_ref}")
        status_task = status_index.get(str(entry.get("task_id")))
        if status_task and str(status_task.get("status")) != "done":
            errors.append(f"{prefix} does not match ai-status {status_task.get('status')}")

    errors.extend(validate_manifest_alignment(payload))

    return errors


def normalize_cell(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^`|`$", "", value)
    return value.strip()


def manifest_closeout_rows() -> list[dict[str, str]]:
    if not MANIFEST_PATH.exists():
        return []

    text = MANIFEST_PATH.read_text(encoding="utf-8")
    marker = "## Remaining Closeout Actions"
    if marker not in text:
        return []
    section = text.split(marker, 1)[1]
    if "\n## " in section:
        section = section.split("\n## ", 1)[0]

    rows: list[dict[str, str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|---") or stripped.startswith("| Task "):
            continue
        cells = [normalize_cell(cell) for cell in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        rows.append(
            {
                "task_id": cells[0],
                "status": cells[1],
                "actor": cells[2],
                "blocking_type": cells[4],
            }
        )
    return rows


def validate_manifest_alignment(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rows = manifest_closeout_rows()
    if not rows:
        return [f"{MANIFEST_PATH.relative_to(ROOT)} missing Remaining Closeout Actions rows"]

    queue_keys = Counter(
        (
            str(entry.get("task_id")),
            str(entry.get("status")),
            str(entry.get("actor")),
            str(entry.get("blocking_type")),
        )
        for entry in payload.get("queue", [])
    )
    manifest_keys = Counter(
        (
            row["task_id"],
            row["status"],
            row["actor"],
            row["blocking_type"],
        )
        for row in rows
        if row["task_id"] != "PR #82" and row["task_id"] != "External proof queue"
    )

    missing = queue_keys - manifest_keys
    extra = manifest_keys - queue_keys
    for key, count in sorted(missing.items()):
        errors.append(f"manifest missing closeout queue row {key} x{count}")
    for key, count in sorted(extra.items()):
        errors.append(f"manifest has stale closeout row {key} x{count}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="store_true",
        help="print a Markdown closeout report in addition to validating the queue",
    )
    args = parser.parse_args()

    payload = load_json(QUEUE_PATH)
    errors = validate_queue(payload)
    if args.report:
        print(render_report(payload, errors), end="")
        return 1 if errors else 0

    if errors:
        print("Product closeout queue check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Product closeout queue checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
