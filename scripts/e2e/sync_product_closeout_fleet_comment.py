#!/usr/bin/env python3
"""Post the current product closeout action matrix to the release PR.

This keeps the fleet-facing PR surface aligned with
PRODUCT_RELEASE_CLOSEOUT_QUEUE.json and live ai-status.json. It does not perform
closeout actions; it tells owners/reviewers/Human-Ops which lifecycle commands
are ready or waiting.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
ACTION_MATRIX_PATH = ROOT / "scripts/e2e/check_product_closeout_action_matrix.py"
E2E_DIR = Path(__file__).resolve().parent
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from _release_target import (
    current_release_head,
    release_label,
    release_pr_comment_args,
    release_pr_view_args,
)

DEFAULT_STATUS_ROOT = (
    Path(os.path.expanduser(os.environ["PANTHEON_STATUS_ROOT"])).resolve()
    if os.environ.get("PANTHEON_STATUS_ROOT")
    else ROOT
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def render_comment(queue_payload: dict[str, Any], rows: list[dict[str, Any]], *, release_sha: str) -> str:
    ready_rows = [row for row in rows if row["readiness"] == "ready"]
    waiting_rows = [row for row in rows if row["readiness"] == "waiting"]
    blocked_rows = [row for row in rows if row["readiness"] in {"blocked_by_pr_checks", "stale_or_invalid"}]
    entries_by_key = {
        (str(entry["task_id"]), str(entry["actor"]), str(entry["action_type"])): entry
        for entry in queue_payload.get("queue", [])
    }

    def append_action_block(row: dict[str, Any], *, suffix: str | None = None) -> None:
        task_id = row["task_id"]
        actor = row["actor"]
        action_type = row["action_type"]
        label = f"- `{task_id}` / `{actor}` / `{action_type}`"
        if suffix:
            label = f"{label} {suffix}"
        lines.extend(
            [
                label,
                "  ```bash",
                (
                    "  PANTHEON_STATUS_ROOT=/home/lupin/oday-plus "
                    f"python3 scripts/e2e/check_product_closeout_action.py --task {task_id} "
                    f"--actor {actor} --action-type {action_type}"
                ),
                "  ```",
            ]
        )
        entry = entries_by_key.get((task_id, actor, action_type), {})
        for command in entry.get("allowed_commands", []):
            lines.append(f"  - allowed command: `{command}`")

    lines = [
        "## Product closeout fleet update",
        "",
        f"Current release target: {release_label(QUEUE_PATH)} headRefOid `{release_sha}`.",
        "",
        "Run the matrix before any lifecycle action:",
        "",
        "```bash",
        "PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/check_product_closeout_action_matrix.py",
        "```",
        "",
        "### Ready lanes",
    ]

    if ready_rows:
        for row in ready_rows:
            append_action_block(row)
    else:
        lines.append("- none")

    lines.extend(["", "### Waiting lanes"])
    if waiting_rows:
        for row in waiting_rows:
            append_action_block(row, suffix=f"waits for `{row['queue_status']}`.")
    else:
        lines.append("- none")

    lines.extend(["", "### Blocked or stale lanes"])
    if blocked_rows:
        for row in blocked_rows:
            append_action_block(row, suffix=f"is `{row['readiness']}`.")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Do not mark product release complete from this comment alone. External proof #132-#138, "
            "Human/Ops go/no-go, and owner/reviewer lifecycle actions must still pass their dedicated gates.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_from_inputs(*, status_path: Path, release_sha: str) -> str:
    matrix = load_module(ACTION_MATRIX_PATH, "check_product_closeout_action_matrix")
    queue_payload = load_json(QUEUE_PATH)
    status_payload = load_json(status_path)
    pr_payload = {
        "number": int(queue_payload["release_target"]["pr"]),
        "state": "OPEN",
        "headRefOid": release_sha,
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"name": "external-render", "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    rows = matrix.evaluate_matrix(queue_payload, status_payload, pr_payload=pr_payload)
    return render_comment(queue_payload, rows, release_sha=release_sha)


def apply_comment(body: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        subprocess.run([*release_pr_comment_args(QUEUE_PATH), "--body-file", str(body_path)], cwd=ROOT, check=True)
    finally:
        body_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", help=f"{release_label(QUEUE_PATH)} headRefOid; defaults to live {release_pr_view_args(QUEUE_PATH, 'headRefOid')}.")
    parser.add_argument(
        "--status-path",
        type=Path,
        default=DEFAULT_STATUS_ROOT / "ai-status.json",
        help="ai-status.json path; defaults to PANTHEON_STATUS_ROOT/ai-status.json or repo ai-status.json",
    )
    parser.add_argument("--output", type=Path, help="write rendered comment body to this file")
    parser.add_argument("--apply", action="store_true", help=f"post the rendered comment to {release_label(QUEUE_PATH)}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release_sha = args.release_sha or current_release_head(root=ROOT, queue_path=QUEUE_PATH)
    body = render_from_inputs(status_path=args.status_path, release_sha=release_sha)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    if args.apply:
        apply_comment(body)
    print(f"rendered product closeout fleet comment for {release_label(QUEUE_PATH)} headRefOid {release_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
