#!/usr/bin/env python3
"""Safely update the external proof handback status board.

Product Validation uses this after a fleet submits a #132-#138 handback. The
tool prevents manual JSON drift: accepted status is only written after the
handback artifact passes the same checker used by the release gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
TEMPLATE_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json"
STATUS_BOARD_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"
ARTIFACT_CHECKER_PATH = ROOT / "scripts/e2e/check_external_proof_handback_artifact.py"
STATUS_CHECKER_PATH = ROOT / "scripts/e2e/check_external_proof_handback_status_board.py"

EDITABLE_STATUSES = {
    "pending_external_handback",
    "handback_submitted",
    "needs_revision",
    "accepted",
}


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_index(status_board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["task_id"]: entry for entry in status_board.get("tasks", [])}


def validate_handback_artifact(handback_path: Path, *, expected_sha: str | None) -> tuple[dict[str, Any], list[str]]:
    artifact_checker = load_module(ARTIFACT_CHECKER_PATH, "check_external_proof_handback_artifact")
    queue = load_json(QUEUE_PATH)
    template = load_json(TEMPLATE_PATH)
    handback = load_json(handback_path)
    queue_entries = {entry["task_id"]: entry for entry in queue.get("queue", [])}
    template_entries = {entry["task_id"]: entry for entry in template.get("tasks", [])}
    errors = artifact_checker.validate_handback(
        handback,
        queue_entries,
        template_entries,
        expected_sha=expected_sha,
    )
    return handback, errors


def apply_update(
    status_board: dict[str, Any],
    *,
    task_id: str,
    status: str,
    handback_path: Path | None,
    expected_sha: str | None,
    next_action: str | None,
) -> list[str]:
    errors: list[str] = []
    if status not in EDITABLE_STATUSES:
        return [f"status must be one of {sorted(EDITABLE_STATUSES)}, got {status!r}"]

    entries = task_index(status_board)
    entry = entries.get(task_id)
    if entry is None:
        return [f"task_id not found in status board: {task_id}"]

    if status == "pending_external_handback":
        entry.update(
            {
                "status": status,
                "handback_artifact_path": None,
                "artifact_check_passed": False,
                "accepted_release_head_ref_oid": None,
                "accepted_at": None,
                "accepted_by": None,
            }
        )
        if next_action:
            entry["next_action"] = next_action
        return errors

    if handback_path is None:
        return [f"{status} requires --handback"]
    if not handback_path.exists():
        return [f"handback file does not exist: {handback_path}"]

    entry["status"] = status
    entry["handback_artifact_path"] = str(handback_path)
    if next_action:
        entry["next_action"] = next_action

    if status in {"handback_submitted", "needs_revision"}:
        entry["artifact_check_passed"] = False
        entry["accepted_release_head_ref_oid"] = None
        entry["accepted_at"] = None
        entry["accepted_by"] = None
        return errors

    handback, validation_errors = validate_handback_artifact(handback_path, expected_sha=expected_sha)
    if validation_errors:
        return [f"accepted status requires a valid handback artifact: {error}" for error in validation_errors]
    if handback.get("task_id") != task_id:
        return [f"handback task_id {handback.get('task_id')!r} does not match --task {task_id!r}"]

    attestation = handback.get("completion_attestation", {})
    entry["artifact_check_passed"] = True
    entry["accepted_release_head_ref_oid"] = handback.get("release_head_ref_oid")
    entry["accepted_at"] = attestation.get("accepted_at")
    entry["accepted_by"] = attestation.get("accepted_by")
    if not next_action:
        entry["next_action"] = "Accepted by Product Validation; keep issue open until bundle and go/no-go gates pass."
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="External proof task id, e.g. ODP-MAP-STAGE-001.")
    parser.add_argument("--status", required=True, choices=sorted(EDITABLE_STATUSES))
    parser.add_argument("--handback", type=Path, help="Handback JSON path for submitted/needs_revision/accepted.")
    parser.add_argument("--expected-sha", help="Expected PR #82 headRefOid for accepted handbacks.")
    parser.add_argument("--next-action", help="Override the task next_action text.")
    parser.add_argument("--status-board", type=Path, default=STATUS_BOARD_PATH, help="Status board JSON to update.")
    parser.add_argument("--check-only", action="store_true", help="Validate the update without writing the file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status_board = load_json(args.status_board)
    errors = apply_update(
        status_board,
        task_id=args.task,
        status=args.status,
        handback_path=args.handback,
        expected_sha=args.expected_sha,
        next_action=args.next_action,
    )

    status_checker = load_module(STATUS_CHECKER_PATH, "check_external_proof_handback_status_board")
    queue = load_json(QUEUE_PATH)
    errors.extend(status_checker.validate_status_board(queue, status_board))

    if errors:
        print("External proof handback status board update failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    if not args.check_only:
        write_json(args.status_board, status_board)

    print("External proof handback status board update checks passed.")
    if args.check_only:
        print("No file was written because --check-only was set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
