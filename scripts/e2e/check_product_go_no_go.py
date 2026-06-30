#!/usr/bin/env python3
"""Validate the product release go/no-go packet boundary.

This is a static guard for the current release state: deterministic product E2E
readiness can be conditionally accepted, but live external providers, remote
map endpoints, and remote staging rollout must remain explicitly conditional
until #132-#138 handbacks are accepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GO_NO_GO_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
EXTERNAL_QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"

REQUIRED_DECISION_TOKENS = (
    "Decision status: conditional go for deterministic product E2E",
    "remote staging rollout remains conditional",
    "Human/Ops",
    "PR #82",
    "headRefOid",
    "attached checks",
)

REQUIRED_CONDITIONAL_TOKENS = (
    "deterministic product-E2E readiness, not staging/production deployment readiness",
    "remote staging host/url/secret owner variables",
    "live staging rollout",
    "PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
    "EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json",
    "provider credential/license/geocoder",
    "remote live map endpoint",
    "remote staging proof",
    "check_external_proof_handback_artifact.py",
    "check_external_proof_handback_status_board.py",
    "check_external_proof_handback_bundle.py",
    "check_external_proof_issue_sync.py --require-assignees",
    "#132-#138",
)

REQUIRED_PENDING_CHECKS = (
    "Remote staging limitation accepted",
    "External proof queue reviewed",
    "External proof handback format reviewed",
    "External proof handback intake reviewed",
    "External proof handback artifacts validated",
    "External proof handback bundle validated",
    "External proof issue sync reviewed",
    "Product go/no-go guard reviewed",
    "Final decision recorded",
)

FORBIDDEN_FULL_RELEASE_PATTERNS = (
    re.compile(r"Decision status:\s*(approved|go|passed)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"Decision status:.*production\s+ready", re.IGNORECASE),
    re.compile(r"Decision status:.*staging\s+ready", re.IGNORECASE),
    re.compile(r"Decision status:.*live\s+remote\s+staging\s+rollout\s+(approved|ready|passed)", re.IGNORECASE),
    re.compile(r"Decision status:.*external\s+proof\s+(accepted|complete|closed)", re.IGNORECASE),
)

FORBIDDEN_COMPLETION_TOKENS = (
    "External proof queue reviewed | Confirm `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`",
    "External proof handback artifacts validated | For each submitted #132-#138 handback",
    "External proof handback bundle validated | After all #132-#138 handbacks are submitted",
    "External proof issue sync reviewed | Run `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees`",
)

REQUIRED_EXTERNAL_TASK_IDS = (
    "ODP-EXT-PROD-001",
    "ODP-EXT-PROD-002",
    "ODP-EXT-PROD-003",
    "ODP-MAP-STAGE-001",
    "ODP-MAP-STAGE-002",
    "ODP-PV-STAGE-001",
    "ODP-PV-STAGE-002",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_pending_row(text: str, row_label: str) -> str | None:
    pattern = re.compile(rf"^\| {re.escape(row_label)} \| .+ \| pending-human \|$", re.MULTILINE)
    if not pattern.search(text):
        return f"go/no-go checklist row must remain pending-human: {row_label}"
    return None


def main() -> int:
    errors: list[str] = []

    if not GO_NO_GO_PATH.exists():
        errors.append(f"missing go/no-go packet: {GO_NO_GO_PATH.relative_to(ROOT)}")
    if not EXTERNAL_QUEUE_PATH.exists():
        errors.append(f"missing external proof queue: {EXTERNAL_QUEUE_PATH.relative_to(ROOT)}")
    if errors:
        print("Product go/no-go guard failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    text = GO_NO_GO_PATH.read_text(encoding="utf-8")
    queue = load_json(EXTERNAL_QUEUE_PATH)

    for token in REQUIRED_DECISION_TOKENS:
        if token not in text:
            errors.append(f"go/no-go packet missing decision token: {token}")

    for token in REQUIRED_CONDITIONAL_TOKENS:
        if token not in text:
            errors.append(f"go/no-go packet missing conditional release boundary token: {token}")

    for row_label in REQUIRED_PENDING_CHECKS:
        row_error = validate_pending_row(text, row_label)
        if row_error:
            errors.append(row_error)

    for pattern in FORBIDDEN_FULL_RELEASE_PATTERNS:
        if pattern.search(text):
            errors.append(f"go/no-go packet contains forbidden full-release decision pattern: {pattern.pattern}")

    for row_prefix in FORBIDDEN_COMPLETION_TOKENS:
        completed_row = re.compile(rf"^\| {re.escape(row_prefix)}.* \| (done|passed|approved|complete) \|$", re.MULTILINE)
        if completed_row.search(text):
            errors.append(f"go/no-go external proof row cannot be marked complete: {row_prefix}")

    release_target = queue.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("external proof queue release_target.pr must be 82")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("external proof queue must forbid hard-coded dev release refs")

    entries = queue.get("queue", [])
    task_ids = {str(entry.get("task_id")) for entry in entries}
    missing_task_ids = set(REQUIRED_EXTERNAL_TASK_IDS) - task_ids
    if missing_task_ids:
        errors.append(f"external proof queue missing tasks: {sorted(missing_task_ids)}")

    for entry in entries:
        task_id = str(entry.get("task_id"))
        if task_id not in REQUIRED_EXTERNAL_TASK_IDS:
            continue
        issue_number = str(entry.get("tracking_issue", "")).rstrip("/").split("/")[-1]
        if f"#{issue_number}" not in text:
            errors.append(f"go/no-go packet missing tracking issue token for {task_id}: #{issue_number}")
        if entry.get("status") != "external_blocked":
            errors.append(f"{task_id} must remain external_blocked until handback acceptance")
        for token in (
            str(entry.get("blocking_type")),
            str(entry.get("owner")),
            str(entry.get("reviewer")),
            str(entry.get("completion_rule")),
        ):
            if token and token not in text:
                errors.append(f"go/no-go packet missing {task_id} queue boundary token: {token}")

    if errors:
        print("Product go/no-go guard failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product go/no-go guard checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
