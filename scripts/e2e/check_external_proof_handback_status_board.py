#!/usr/bin/env python3
"""Validate the external proof handback intake status board.

The closeout queue says what fleets must prove. The handback status board says
whether Product Validation has received and accepted each proof artifact. This
checker keeps that board synchronized with #132-#138 and prevents accidental
"accepted" claims without artifact paths and validation commands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
STATUS_BOARD_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"

ALLOWED_TASK_STATUSES = {
    "pending_external_handback",
    "handback_submitted",
    "needs_revision",
    "accepted",
}

ALLOWED_BUNDLE_STATUSES = {
    "pending_external_handbacks",
    "partial_handbacks_submitted",
    "needs_revision",
    "accepted",
}

REQUIRED_GLOBAL_RULE_TOKENS = (
    "check_external_proof_handback_artifact.py",
    "update_external_proof_handback_status_board.py",
    "check_external_proof_handback_bundle.py",
    "#132-#138",
    "Do not use this board as runtime proof",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_status_board(queue: dict[str, Any], status_board: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    release_target = status_board.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("handback status board release_target.pr must be 82")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("handback status board must forbid hard-coded dev release refs")
    if status_board.get("source_of_truth") != "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json":
        errors.append("handback status board must point to the external proof closeout queue")

    status_values = set(status_board.get("status_values", []))
    missing_statuses = ALLOWED_TASK_STATUSES - status_values
    if missing_statuses:
        errors.append(f"handback status board missing status_values: {sorted(missing_statuses)}")

    global_rules = "\n".join(str(rule) for rule in status_board.get("global_rules", []))
    for token in REQUIRED_GLOBAL_RULE_TOKENS:
        if token not in global_rules:
            errors.append(f"handback status board global_rules missing token: {token}")

    bundle = status_board.get("bundle_status", {})
    bundle_status = str(bundle.get("status", ""))
    if bundle_status not in ALLOWED_BUNDLE_STATUSES:
        errors.append(f"bundle_status.status must be one of {sorted(ALLOWED_BUNDLE_STATUSES)}, got {bundle_status!r}")
    if "check_external_proof_handback_bundle.py" not in str(bundle.get("validated_by_command", "")):
        errors.append("bundle_status.validated_by_command must run check_external_proof_handback_bundle.py")
    if bundle_status == "accepted":
        for field in ("handback_bundle_path", "accepted_release_head_ref_oid", "last_validated_at"):
            if not bundle.get(field):
                errors.append(f"accepted bundle_status missing {field}")
    else:
        for field in ("handback_bundle_path", "accepted_release_head_ref_oid", "last_validated_at"):
            if bundle.get(field):
                errors.append(f"non-accepted bundle_status must not set {field}")

    queue_entries = {entry["task_id"]: entry for entry in queue.get("queue", [])}
    status_entries = {entry.get("task_id"): entry for entry in status_board.get("tasks", [])}
    if set(queue_entries) != set(status_entries):
        errors.append(
            "handback status board task ids must match external proof queue: "
            f"missing={sorted(set(queue_entries) - set(status_entries))}, "
            f"extra={sorted(set(status_entries) - set(queue_entries))}"
        )

    for task_id, queue_entry in sorted(queue_entries.items()):
        entry = status_entries.get(task_id, {})
        prefix = f"{task_id}"
        if entry.get("tracking_issue") != queue_entry.get("tracking_issue"):
            errors.append(f"{prefix} tracking_issue must match external proof queue")
        status = str(entry.get("status", ""))
        if status not in ALLOWED_TASK_STATUSES:
            errors.append(f"{prefix} status must be one of {sorted(ALLOWED_TASK_STATUSES)}, got {status!r}")
        if "check_external_proof_handback_artifact.py" not in str(entry.get("artifact_check_command", "")):
            errors.append(f"{prefix} artifact_check_command must run check_external_proof_handback_artifact.py")
        if "gh pr view 82 --json headRefOid" not in str(entry.get("artifact_check_command", "")):
            errors.append(f"{prefix} artifact_check_command must bind expected-sha to PR #82 headRefOid")
        if not entry.get("next_action"):
            errors.append(f"{prefix} missing next_action")

        if status == "pending_external_handback":
            if entry.get("handback_artifact_path") is not None:
                errors.append(f"{prefix} pending handback must not set handback_artifact_path")
            if entry.get("artifact_check_passed") is not False:
                errors.append(f"{prefix} pending handback must set artifact_check_passed false")
            for field in ("accepted_release_head_ref_oid", "accepted_at", "accepted_by"):
                if entry.get(field) is not None:
                    errors.append(f"{prefix} pending handback must not set {field}")

        if status in {"handback_submitted", "needs_revision", "accepted"}:
            if not entry.get("handback_artifact_path"):
                errors.append(f"{prefix} {status} must set handback_artifact_path")

        if status == "accepted":
            if entry.get("artifact_check_passed") is not True:
                errors.append(f"{prefix} accepted handback must set artifact_check_passed true")
            for field in ("accepted_release_head_ref_oid", "accepted_at", "accepted_by"):
                if not entry.get(field):
                    errors.append(f"{prefix} accepted handback missing {field}")
        elif status != "pending_external_handback":
            if entry.get("accepted_release_head_ref_oid") or entry.get("accepted_at") or entry.get("accepted_by"):
                errors.append(f"{prefix} non-accepted handback must not set accepted metadata")

    return errors


def main() -> int:
    errors: list[str] = []

    if not QUEUE_PATH.exists():
        errors.append(f"missing external proof queue: {QUEUE_PATH.relative_to(ROOT)}")
        queue: dict[str, Any] = {"queue": []}
    else:
        queue = load_json(QUEUE_PATH)

    if not STATUS_BOARD_PATH.exists():
        errors.append(f"missing external proof handback status board: {STATUS_BOARD_PATH.relative_to(ROOT)}")
        status_board: dict[str, Any] = {"tasks": []}
    else:
        status_board = load_json(STATUS_BOARD_PATH)

    errors.extend(validate_status_board(queue, status_board))

    if errors:
        print("External proof handback status board checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof handback status board checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
