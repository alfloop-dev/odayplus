#!/usr/bin/env python3
"""Validate the external proof fleet pickup board against the closeout queue.

The external proof queue is machine-readable. The pickup board is the
operator/fleet-facing surface. This checker keeps them synchronized so #132-#138
cannot lose issue routing, handback commands, or closeout boundaries in prose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
BOARD_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md"

REQUIRED_BOARD_TOKENS = (
    "External Proof Fleet Pickup Board",
    "PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
    "gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url",
    "python3 scripts/e2e/check_external_proof_closeout_queue.py",
    "python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees",
    "python3 scripts/e2e/check_external_proof_handback_template.py",
    "python3 scripts/e2e/check_external_proof_handback_status_board.py",
    "python3 scripts/e2e/update_external_proof_handback_status_board.py",
    "python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees",
    "python3 scripts/e2e/check_external_proof_fleet_notifications.py",
    "python3 scripts/e2e/check_external_proof_fleet_pickup_board.py",
    "python3 scripts/e2e/check_product_go_no_go.py",
    "python3 scripts/e2e/check_external_proof_handback_artifact.py",
    "python3 scripts/e2e/check_external_proof_handback_bundle.py",
    "PRODUCT_RELEASE_GO_NO_GO.md",
    "EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json",
    "completion_attestation.decision",
    "contains_secret_values: false",
    "mock://",
    "localhost",
    "127.0.0.1",
    "Do not close #132-#138",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def command_fragment(command: str) -> str:
    return command.split(" <")[0].split(' "<')[0]


def validate(queue_payload: dict[str, Any], board_text: str) -> list[str]:
    errors: list[str] = []

    release_target = queue_payload.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("external proof queue release_target.pr must be 82")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("external proof queue must forbid hard-coded dev release refs")

    for token in REQUIRED_BOARD_TOKENS:
        if token not in board_text:
            errors.append(f"external proof pickup board missing token: {token}")

    entries = queue_payload.get("queue", [])
    if not isinstance(entries, list) or not entries:
        errors.append("external proof closeout queue must contain entries")
        entries = []

    for index, entry in enumerate(entries):
        task_id = str(entry.get("task_id", ""))
        prefix = f"queue[{index}] {task_id}"
        issue_number = str(entry.get("tracking_issue", "")).rstrip("/").rsplit("/", 1)[-1]
        routing = entry.get("fleet_routing", {})
        if not isinstance(routing, dict):
            errors.append(f"{prefix} fleet_routing must be an object")
            routing = {}

        for token in (
            task_id,
            f"#{issue_number}",
            str(routing.get("dispatch_lane", "")),
            str(routing.get("pickup_label", "")),
            str(entry.get("completion_rule", "")),
        ):
            if token and token not in board_text:
                errors.append(f"external proof pickup board missing {prefix} token: {token}")

        for label in routing.get("required_issue_labels", []):
            label_token = f"`{label}`"
            if label_token not in board_text:
                errors.append(f"external proof pickup board missing {prefix} label: {label_token}")

        for command in entry.get("handback_commands", []):
            fragment = command_fragment(str(command))
            if fragment not in board_text:
                errors.append(f"external proof pickup board missing {prefix} handback command: {fragment}")

        acceptance_command = (
            'python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha '
            '"$(gh pr view 82 --json headRefOid --jq .headRefOid)"'
        )
        if acceptance_command not in board_text:
            errors.append(f"external proof pickup board missing {prefix} acceptance command")

        for evidence in entry.get("required_evidence", []):
            if str(evidence) not in board_text:
                errors.append(f"external proof pickup board missing {prefix} required evidence: {evidence}")

    return errors


def main() -> int:
    errors: list[str] = []

    if not QUEUE_PATH.exists():
        errors.append(f"missing external proof queue: {QUEUE_PATH.relative_to(ROOT)}")
        queue_payload: dict[str, Any] = {"queue": []}
    else:
        queue_payload = load_json(QUEUE_PATH)

    if not BOARD_PATH.exists():
        errors.append(f"missing external proof pickup board: {BOARD_PATH.relative_to(ROOT)}")
        board_text = ""
    else:
        board_text = BOARD_PATH.read_text(encoding="utf-8")

    errors.extend(validate(queue_payload, board_text))

    if errors:
        print("External proof fleet pickup board checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("External proof fleet pickup board checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
