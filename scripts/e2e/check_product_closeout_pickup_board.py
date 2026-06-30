#!/usr/bin/env python3
"""Validate the product release closeout pickup board.

The closeout queue is the machine-readable source of truth. The pickup board is
the fleet/operator-facing surface. This checker keeps them synchronized so a
release owner can trust the board without running the broader pytest suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
BOARD_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md"

REQUIRED_BOARD_TOKENS = (
    "Product Release Closeout Pickup Board",
    "PRODUCT_RELEASE_CLOSEOUT_QUEUE.json",
    "PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md",
    "PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md",
    "EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md",
    "gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url",
    "python3 scripts/e2e/check_product_release_gate.py",
    "python3 scripts/e2e/check_product_closeout_queue.py --report",
    "python3 -m pytest tests/e2e/test_frontend_execution_matrix_coverage.py",
    "python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees",
    "python3 scripts/e2e/check_external_proof_handback_artifact.py",
    "provider-specific production credential",
    "provider-specific production licensing approval",
    "remote-staging live tile",
    "remote staging host/url/secret",
    "full keyboard accessibility",
    "Do not mark the product release objective complete",
)

REQUIRED_ACTION_TOKENS = (
    "go_no_go",
    "owner_handoff",
    "owner_done",
    "reviewer_approve_or_reopen",
    "scripts/ai_status.py handoff",
    "scripts/ai_status.py approve",
    "scripts/ai_status.py reopen",
    "scripts/ai_status.py done",
    "REVIEW_NOTES_ZH",
)

REQUIRED_ACTORS = (
    "Human/Ops",
    "Claude",
    "Claude2",
    "Codex",
    "Codex2",
)

REQUIRED_BLOCKING_TYPES = (
    "human_signoff",
    "owner_status_closeout",
    "reviewer_status_closeout",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def command_fragment(command: str) -> str:
    """Return a stable fragment for commands containing placeholders."""
    return command.split(" <")[0].split(' "<')[0]


def main() -> int:
    errors: list[str] = []

    if not QUEUE_PATH.exists():
        errors.append(f"missing closeout queue: {QUEUE_PATH.relative_to(ROOT)}")
        queue_payload: dict[str, Any] = {"queue": []}
    else:
        queue_payload = load_json(QUEUE_PATH)

    if not BOARD_PATH.exists():
        errors.append(f"missing pickup board: {BOARD_PATH.relative_to(ROOT)}")
        board_text = ""
    else:
        board_text = BOARD_PATH.read_text(encoding="utf-8")

    release_target = queue_payload.get("release_target", {})
    if release_target.get("pr") != 82:
        errors.append("closeout queue release_target.pr must be 82")
    if release_target.get("must_not_hardcode_dev_hash") is not True:
        errors.append("closeout queue must forbid hard-coded dev release refs")

    for token in REQUIRED_BOARD_TOKENS:
        if token not in board_text:
            errors.append(f"pickup board missing token: {token}")

    for token in REQUIRED_ACTION_TOKENS:
        if token not in board_text:
            errors.append(f"pickup board missing action token: {token}")

    for actor in REQUIRED_ACTORS:
        if actor not in board_text:
            errors.append(f"pickup board missing actor: {actor}")

    for blocking_type in REQUIRED_BLOCKING_TYPES:
        if blocking_type not in board_text:
            errors.append(f"pickup board missing blocking type: {blocking_type}")

    entries = queue_payload.get("queue", [])
    if not isinstance(entries, list) or not entries:
        errors.append("closeout queue must contain entries")
        entries = []

    for index, entry in enumerate(entries):
        prefix = f"queue[{index}] {entry.get('task_id')}"
        for field in ("task_id", "status", "actor", "action_type", "blocking_type"):
            value = str(entry.get(field, ""))
            if not value:
                errors.append(f"{prefix} missing {field}")
            elif value not in board_text:
                errors.append(f"pickup board missing {prefix} {field}: {value}")

        for evidence_ref in entry.get("evidence_refs", []):
            evidence_ref = str(evidence_ref)
            if evidence_ref not in board_text:
                errors.append(f"pickup board missing {prefix} evidence ref: {evidence_ref}")

        for command in entry.get("allowed_commands", []):
            fragment = command_fragment(str(command))
            if fragment not in board_text:
                errors.append(f"pickup board missing {prefix} command fragment: {fragment}")

    if errors:
        print("Product closeout pickup board checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product closeout pickup board checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
