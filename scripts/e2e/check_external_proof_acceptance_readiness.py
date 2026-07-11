#!/usr/bin/env python3
"""Report Product Validation readiness for external-proof handbacks.

The external proof queue dispatches #132-#138 to fleets. The handback status
board tracks intake. This checker joins both surfaces into one acceptance
readiness report so Product Validation can see exactly which runtime proof is
still missing and which command accepts it when submitted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
STATUS_BOARD_PATH = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def command_fragment(command: str) -> str:
    return command.split(" <")[0].split(' "<')[0]


def evaluate_readiness(queue: dict[str, Any], status_board: dict[str, Any]) -> list[dict[str, Any]]:
    status_entries = {entry["task_id"]: entry for entry in status_board.get("tasks", [])}
    rows: list[dict[str, Any]] = []
    for entry in queue.get("queue", []):
        task_id = str(entry["task_id"])
        status_entry = status_entries.get(task_id, {})
        status = str(status_entry.get("status", "missing_status_board_entry"))
        required_evidence = [str(item) for item in entry.get("required_evidence", [])]
        handback_commands = [str(item) for item in entry.get("handback_commands", [])]
        acceptance_command = next(
            (command for command in handback_commands if "check_external_proof_handback_artifact.py" in command),
            "",
        )
        skeleton_command = next(
            (command for command in handback_commands if "generate_external_proof_handback_skeleton.py" in command),
            "",
        )
        missing_evidence = [] if status == "accepted" else required_evidence
        rows.append(
            {
                "task_id": task_id,
                "tracking_issue": str(entry.get("tracking_issue", "")),
                "fleet_lane": str(entry.get("fleet_routing", {}).get("dispatch_lane", "")),
                "status": status,
                "blocking_type": str(entry.get("blocking_type", "")),
                "required_evidence": required_evidence,
                "missing_evidence": missing_evidence,
                "next_action": str(status_entry.get("next_action", "")),
                "handback_artifact_path": status_entry.get("handback_artifact_path"),
                "artifact_check_passed": status_entry.get("artifact_check_passed"),
                "skeleton_command": skeleton_command,
                "acceptance_command": acceptance_command,
                "completion_rule": str(entry.get("completion_rule", "")),
            }
        )
    return rows


def validate_readiness(
    queue: dict[str, Any],
    status_board: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    strict_complete: bool = False,
) -> list[str]:
    errors: list[str] = []
    queue_task_ids = {entry["task_id"] for entry in queue.get("queue", [])}
    status_task_ids = {entry["task_id"] for entry in status_board.get("tasks", [])}
    if queue_task_ids != status_task_ids:
        errors.append(
            "status board task ids must match external proof queue: "
            f"missing={sorted(queue_task_ids - status_task_ids)}, extra={sorted(status_task_ids - queue_task_ids)}"
        )

    for row in rows:
        prefix = row["task_id"]
        if not row["tracking_issue"]:
            errors.append(f"{prefix} missing tracking_issue")
        if not row["fleet_lane"]:
            errors.append(f"{prefix} missing fleet lane")
        if not row["required_evidence"]:
            errors.append(f"{prefix} missing required evidence list")
        if not row["next_action"]:
            errors.append(f"{prefix} missing next_action")
        if "generate_external_proof_handback_skeleton.py" not in row["skeleton_command"]:
            errors.append(f"{prefix} missing skeleton generation command")
        if "check_external_proof_handback_artifact.py" not in row["acceptance_command"]:
            errors.append(f"{prefix} missing handback artifact acceptance command")
        if "gh pr view 82 --json headRefOid" not in row["acceptance_command"]:
            errors.append(f"{prefix} acceptance command must bind expected-sha to PR #82 headRefOid")
        if not row["completion_rule"]:
            errors.append(f"{prefix} missing completion_rule")

        if row["status"] == "accepted":
            if row["missing_evidence"]:
                errors.append(f"{prefix} accepted row must not have missing evidence")
            if row["artifact_check_passed"] is not True:
                errors.append(f"{prefix} accepted row must have artifact_check_passed true")
            if not row["handback_artifact_path"]:
                errors.append(f"{prefix} accepted row must set handback_artifact_path")
        elif strict_complete:
            errors.append(f"{prefix} is not accepted: {row['status']}")

    bundle_status = status_board.get("bundle_status", {})
    if strict_complete and bundle_status.get("status") != "accepted":
        errors.append("bundle_status must be accepted in --strict-complete mode")
    if "check_external_proof_handback_bundle.py" not in str(bundle_status.get("validated_by_command", "")):
        errors.append("bundle_status.validated_by_command must run check_external_proof_handback_bundle.py")

    return errors


def render_markdown(rows: list[dict[str, Any]], status_board: dict[str, Any]) -> str:
    lines = [
        "# External Proof Acceptance Readiness",
        "",
        f"Bundle status: `{status_board.get('bundle_status', {}).get('status')}`",
        "",
        "| Task | Issue | Fleet Lane | Status | Missing Evidence | Next Action |",
        "|---|---|---|---|---:|---|",
    ]
    for row in rows:
        issue = row["tracking_issue"].rstrip("/").rsplit("/", 1)[-1]
        lines.append(
            f"| `{row['task_id']}` | #{issue} | {row['fleet_lane']} | `{row['status']}` | "
            f"{len(row['missing_evidence'])} | {row['next_action']} |"
        )

    lines.extend(["", "## Acceptance Commands", ""])
    for row in rows:
        lines.extend(
            [
                f"### `{row['task_id']}`",
                "",
                "Skeleton:",
                "",
                "```bash",
                row["skeleton_command"],
                "```",
                "",
                "Accept only after this passes:",
                "",
                "```bash",
                row["acceptance_command"],
                "```",
                "",
                "Required evidence:",
            ]
        )
        for evidence in row["required_evidence"]:
            lines.append(f"- {evidence}")
        lines.extend(["", f"Completion rule: {row['completion_rule']}", ""])
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON rows")
    parser.add_argument("--report", action="store_true", help="emit Markdown report")
    parser.add_argument(
        "--strict-complete",
        action="store_true",
        help="fail unless every task and bundle_status are accepted",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue = load_json(QUEUE_PATH)
    status_board = load_json(STATUS_BOARD_PATH)
    rows = evaluate_readiness(queue, status_board)
    errors = validate_readiness(queue, status_board, rows, strict_complete=args.strict_complete)

    if args.json:
        print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2))
    elif args.report:
        print(render_markdown(rows, status_board), end="")
    elif errors:
        print("External proof acceptance readiness checks failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("External proof acceptance readiness checks passed.")
        pending = [row for row in rows if row["status"] != "accepted"]
        if pending:
            print(f"Pending external handbacks: {len(pending)}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
