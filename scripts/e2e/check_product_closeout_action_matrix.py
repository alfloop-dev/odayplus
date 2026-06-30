#!/usr/bin/env python3
"""Report readiness for every product closeout lifecycle action.

Single-action preflight answers "may this actor run this action now?". This
matrix summarizes every active queue row so fleets can see which actions are
ready, which are correctly waiting for a handoff, and which are stale.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
ACTION_CHECKER_PATH = ROOT / "scripts/e2e/check_product_closeout_action.py"
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


def classify_errors(errors: list[str]) -> str:
    if not errors:
        return "ready"
    joined = "\n".join(errors)
    if " is not ready:" in joined:
        return "waiting"
    if "PR #82 check" in joined or "mergeStateStatus must be CLEAN" in joined:
        return "blocked_by_pr_checks"
    return "stale_or_invalid"


def evaluate_matrix(
    queue_payload: dict[str, Any],
    status_payload: dict[str, Any],
    *,
    pr_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    action_checker = load_module(ACTION_CHECKER_PATH, "check_product_closeout_action")
    rows: list[dict[str, Any]] = []

    for entry in queue_payload.get("queue", []):
        task_id = str(entry.get("task_id"))
        actor = str(entry.get("actor"))
        action_type = str(entry.get("action_type"))
        errors = action_checker.validate_closeout_action(
            queue_payload,
            status_payload,
            task_id=task_id,
            actor=actor,
            action_type=action_type,
            pr_payload=pr_payload,
        )
        rows.append(
            {
                "task_id": task_id,
                "actor": actor,
                "action_type": action_type,
                "queue_status": str(entry.get("status")),
                "blocking_type": str(entry.get("blocking_type")),
                "readiness": classify_errors(errors),
                "errors": errors,
            }
        )
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Product Closeout Action Matrix",
        "",
        "| Task | Actor | Action | Queue Status | Blocking Type | Readiness |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['task_id']} | {row['actor']} | {row['action_type']} | "
            f"{row['queue_status']} | {row['blocking_type']} | {row['readiness']} |"
        )

    problem_rows = [row for row in rows if row["readiness"] in {"blocked_by_pr_checks", "stale_or_invalid"}]
    if problem_rows:
        lines.extend(["", "## Blocking Details", ""])
        for row in problem_rows:
            lines.append(f"### {row['task_id']} / {row['actor']} / {row['action_type']}")
            for error in row["errors"]:
                lines.append(f"- {error}")
            lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=QUEUE_PATH)
    parser.add_argument(
        "--status-path",
        type=Path,
        default=DEFAULT_STATUS_ROOT / "ai-status.json",
        help="ai-status.json path; defaults to PANTHEON_STATUS_ROOT/ai-status.json or repo ai-status.json",
    )
    parser.add_argument("--pr-json", type=Path, help="Fixture PR #82 JSON payload for deterministic tests")
    parser.add_argument("--skip-pr-check", action="store_true", help="skip live PR #82 validation")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    action_checker = load_module(ACTION_CHECKER_PATH, "check_product_closeout_action")
    queue_payload = load_json(args.queue)
    status_payload = load_json(args.status_path)
    pr_payload = action_checker.load_pr82_payload(args.pr_json, skip_live=args.skip_pr_check)
    rows = evaluate_matrix(queue_payload, status_payload, pr_payload=pr_payload)

    if args.json:
        print(json.dumps({"rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(rows), end="")

    invalid = [row for row in rows if row["readiness"] in {"blocked_by_pr_checks", "stale_or_invalid"}]
    if invalid:
        return 1
    print("Product closeout action matrix checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
