#!/usr/bin/env python3
"""Render or post escalation comments for overdue external-proof handbacks.

The issue handback scanner decides whether a task is overdue. This tool turns
those rows into a consistent release-owner escalation comment, without changing
the acceptance state. It is intentionally separate from the pickup syncer:
pickup tells fleets what to do, escalation tells them a handback is late or
needs attention.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
SCAN_PATH = ROOT / "delivery_toolchain/e2e/check_external_proof_issue_handback_scan.py"

E2E_DIR = Path(__file__).resolve().parent
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from _release_target import current_release_head, release_label, release_pr_head_command
from _support import issue_number_from_url, load_json, load_module, run_gh_with_retry


def render_escalation_comment(
    *,
    queue_entry: dict[str, Any],
    scan_row: dict[str, Any],
    expected_sha: str,
    escalation_hours: float,
) -> str:
    task_id = str(queue_entry["task_id"])
    issue = str(scan_row["issue"])
    age = scan_row.get("pickup_age_hours")
    age_text = f"{float(age):.1f}h" if isinstance(age, int | float) else "unknown"
    required_evidence = "\n".join(f"- [ ] {item}" for item in queue_entry.get("required_evidence", []))
    handback_commands = "\n".join(str(command) for command in queue_entry.get("handback_commands", []))
    escalation = str(queue_entry.get("fleet_routing", {}).get("escalation", ""))
    generated_date = datetime.now(UTC).date().isoformat()

    return f"""## External proof handback escalation - {generated_date}

Task: `{task_id}` ({issue})
Release target: {release_label(QUEUE_PATH)} headRefOid `{expected_sha}`

Status: `{scan_row['status']}`
Latest pickup: `{scan_row.get('latest_pickup_created_at')}`
Age since pickup: `{age_text}` (threshold `{escalation_hours:g}h`)
Candidate handback comments after pickup: `{len(scan_row.get('candidate_handback_comments', []))}`

### Required runtime evidence still missing
{required_evidence}

### Handback commands
```bash
{handback_commands}
python3 delivery_toolchain/e2e/check_external_proof_acceptance_readiness.py --report
```

### Escalation owner
{escalation}

Product Validation cannot accept or close this blocker until the handback artifact passes:

```bash
python3 delivery_toolchain/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$({release_pr_head_command(QUEUE_PATH)})"
```
"""


def rows_to_escalate(
    queue: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    force: bool,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    queue_entries = {str(entry["task_id"]): entry for entry in queue.get("queue", [])}
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        if force or row.get("escalation_due") is True:
            entry = queue_entries.get(str(row.get("task_id")))
            if entry is not None:
                selected.append((entry, row))
    return selected


def build_scan_rows(*, expected_sha: str, escalation_hours: float) -> list[dict[str, Any]]:
    scanner = load_module(SCAN_PATH, "check_external_proof_issue_handback_scan")
    queue = load_json(QUEUE_PATH)
    issue_numbers = [
        issue_number_from_url(str(entry["tracking_issue"]))
        for entry in queue.get("queue", [])
    ]
    issues_by_number = {issue_number: scanner.load_issue(issue_number) for issue_number in issue_numbers}
    return scanner.scan_queue(
        queue,
        expected_sha=expected_sha,
        issues_by_number=issues_by_number,
        now=datetime.now(UTC),
        escalation_hours=escalation_hours,
    )


def post_issue_comment(issue_number: str, body: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        run_gh(["gh", "issue", "comment", issue_number, "--body-file", str(body_path)])
    finally:
        body_path.unlink(missing_ok=True)


def run_gh(args: list[str], *, attempts: int = 3) -> None:
    run_gh_with_retry(
        args,
        root=ROOT,
        attempts=attempts,
        runner=subprocess.run,
        sleeper=time.sleep,
    )


def escalation_comment_already_posted(issue: dict[str, Any], *, task_id: str, expected_sha: str) -> bool:
    for comment in issue.get("comments", []):
        body = str(comment.get("body", ""))
        if (
            "External proof handback escalation" in body
            and task_id in body
            and expected_sha in body
        ):
            return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", help="expected release PR headRefOid; defaults to live gh pr view")
    parser.add_argument("--escalation-hours", type=float, default=24.0)
    parser.add_argument("--force", action="store_true", help="render/post all pending rows even if not overdue")
    parser.add_argument("--apply", action="store_true", help="post escalation comments to GitHub issues")
    parser.add_argument("--comment-dir", type=Path, help="write rendered escalation comments to this directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha or current_release_head(root=ROOT, queue_path=QUEUE_PATH)
    queue = load_json(QUEUE_PATH)
    rows = build_scan_rows(expected_sha=expected_sha, escalation_hours=args.escalation_hours)
    selected = rows_to_escalate(queue, rows, force=args.force)

    if args.comment_dir:
        args.comment_dir.mkdir(parents=True, exist_ok=True)

    for entry, row in selected:
        body = render_escalation_comment(
            queue_entry=entry,
            scan_row=row,
            expected_sha=expected_sha,
            escalation_hours=args.escalation_hours,
        )
        task_id = str(entry["task_id"])
        issue_number = str(row["issue"]).lstrip("#")
        if args.comment_dir:
            (args.comment_dir / f"{task_id}.md").write_text(body, encoding="utf-8")
        if args.apply:
            issue = load_module(SCAN_PATH, "check_external_proof_issue_handback_scan").load_issue(issue_number)
            if escalation_comment_already_posted(issue, task_id=task_id, expected_sha=expected_sha):
                print(f"skipped {task_id} escalation -> issue #{issue_number}; current release escalation already posted")
                continue
            post_issue_comment(issue_number, body)
            print(f"posted {task_id} escalation -> issue #{issue_number}")
        else:
            print(f"rendered {task_id} escalation for issue #{issue_number}")

    if not selected:
        print("No external proof handback escalations due.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
