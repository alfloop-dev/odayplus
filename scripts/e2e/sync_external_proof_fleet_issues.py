#!/usr/bin/env python3
"""Render or apply GitHub issue handoff text for external-proof blockers.

The source of truth is PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json. This tool
keeps #132-#138 issue bodies and fleet pickup comments aligned with the current
PR #82 headRefOid, so a new release-candidate commit cannot leave external
proof workers following stale handoff instructions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"


def load_queue(path: Path = QUEUE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def issue_number_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def current_pr82_head() -> str:
    raw = subprocess.check_output(
        ["gh", "pr", "view", "82", "--json", "headRefOid", "--jq", ".headRefOid"],
        cwd=ROOT,
        text=True,
    )
    return raw.strip()


def shell_block(command: str) -> str:
    return f"```bash\n{command}\n```"


def inline_labels(labels: list[Any]) -> str:
    return ", ".join(f"`{label}`" for label in labels)


def render_issue_title(entry: dict[str, Any]) -> str:
    return f"[{entry['task_id']}] {entry['title']}"


def render_issue_body(entry: dict[str, Any]) -> str:
    task_id = str(entry["task_id"])
    routing = entry["fleet_routing"]
    required_evidence = "\n".join(f"- [ ] {item}" for item in entry["required_evidence"])
    allowed_commands = "\n\n".join(shell_block(str(command)) for command in entry["allowed_commands"])
    evidence_refs = "\n".join(f"- `{ref}`" for ref in entry["evidence_refs"])

    return "\n".join(
        [
            f"Task: `{task_id}`",
            "",
            "## Fleet pickup routing",
            f"- Dispatch lane: `{routing['dispatch_lane']}`",
            f"- Pickup label: `{routing['pickup_label']}`",
            f"- Required issue labels: {inline_labels(routing['required_issue_labels'])}",
            f"- Pickup command: `{routing['pickup_command']}`",
            f"- Release authority: {routing['release_authority']}",
            f"- Escalation: {routing['escalation']}",
            "- Fleet pickup board: `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md`",
            "",
            "## Runtime proof handback format",
            "- Use `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` for attached runtime proof.",
            "- Use `docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json` as a redacted shape example, not as live proof.",
            "- Product Validation tracks intake status in `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`.",
            (
                "- Generate a task-specific starter with "
                f"`python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task {task_id} "
                "--release-sha-from-pr82 --output <handback.json>`."
            ),
            "- Run `python3 scripts/e2e/check_external_proof_handback_template.py` before requesting Product Validation acceptance.",
            "- Run `python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report` to see the current missing-evidence report and acceptance commands.",
            "- `python3 scripts/e2e/check_external_proof_acceptance_readiness.py --strict-complete` is expected to fail until all #132-#138 handbacks and the bundle status are accepted.",
            (
                "- Run "
                f"`python3 scripts/e2e/update_external_proof_handback_status_board.py --task {task_id} "
                "--status handback_submitted --handback <handback.json>` when Product Validation receives a handback."
            ),
            "- Run `python3 scripts/e2e/check_external_proof_handback_status_board.py` after updating intake status.",
            "- Run `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees` before closing this issue so unaccepted handbacks keep open release-blocker issues.",
            '- Run `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` before accepting or closing this issue.',
            '- After all #132-#138 handbacks are submitted, Product Validation runs `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` before release closeout.',
            "- Before go/no-go, Product Validation runs `python3 scripts/e2e/check_product_go_no_go.py` and confirms `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` still marks #132-#138 as pending external proof.",
            "",
            f"Owner: `{entry['owner']}`",
            f"Reviewer: `{entry['reviewer']}`",
            f"Blocking type: `{entry['blocking_type']}`",
            "",
            "## Required evidence",
            required_evidence,
            "",
            "## Allowed commands",
            allowed_commands,
            "",
            "## Evidence refs",
            evidence_refs,
            "",
            "## Completion rule",
            str(entry["completion_rule"]),
        ]
    )


def render_pickup_comment(entry: dict[str, Any], release_sha: str) -> str:
    task_id = str(entry["task_id"])
    required_evidence = "\n".join(f"- [ ] {item}" for item in entry["required_evidence"])
    allowed_commands = "\n".join(f"- `{command}`" for command in entry["allowed_commands"])
    handback_commands = "\n".join(str(command) for command in entry["handback_commands"])
    generated_date = datetime.now(UTC).date().isoformat()
    return f"""## External proof fleet pickup update - {generated_date}

Current release target: PR #82 headRefOid `{release_sha}`.

Task: `{task_id}`

### Required runtime evidence
{required_evidence}

### Minimum commands/proof to attach
{allowed_commands}

### Handback flow
```bash
{handback_commands}
python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report
python3 scripts/e2e/check_external_proof_acceptance_readiness.py --strict-complete
python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees
```

`--strict-complete` is expected to fail until every #132-#138 handback and the bundle status are accepted.

Do not close this issue from deterministic fixtures, mock-live evidence, localhost proof, or document-only evidence. Completion rule: {entry["completion_rule"]}
"""


def select_entries(queue_payload: dict[str, Any], task_ids: set[str] | None) -> list[dict[str, Any]]:
    entries = list(queue_payload.get("queue", []))
    if task_ids is None:
        return entries
    selected = [entry for entry in entries if str(entry.get("task_id")) in task_ids]
    found = {str(entry.get("task_id")) for entry in selected}
    missing = sorted(task_ids - found)
    if missing:
        raise SystemExit(f"Unknown external proof task id(s): {', '.join(missing)}")
    return selected


def write_rendered_files(
    entries: list[dict[str, Any]],
    *,
    release_sha: str,
    issue_body_dir: Path | None,
    comment_body_dir: Path | None,
) -> None:
    for directory in (issue_body_dir, comment_body_dir):
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        task_id = str(entry["task_id"])
        if issue_body_dir:
            (issue_body_dir / f"{task_id}.md").write_text(render_issue_body(entry), encoding="utf-8")
        if comment_body_dir:
            (comment_body_dir / f"{task_id}.md").write_text(
                render_pickup_comment(entry, release_sha),
                encoding="utf-8",
            )


def run_gh_with_body(args: list[str], body: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        run_gh(args + ["--body-file", str(body_path)])
    finally:
        body_path.unlink(missing_ok=True)


def run_gh(args: list[str], *, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        result = subprocess.run(args, cwd=ROOT, check=False)
        if result.returncode == 0:
            return
        if attempt == attempts:
            raise subprocess.CalledProcessError(result.returncode, args)
        time.sleep(2 * attempt)


def load_issue(issue_number: str) -> dict[str, Any]:
    args = ["gh", "issue", "view", issue_number, "--json", "number,comments"]
    for attempt in range(1, 4):
        result = subprocess.run(args, cwd=ROOT, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        if attempt == 3:
            raise subprocess.CalledProcessError(result.returncode, args, output=result.stdout, stderr=result.stderr)
        time.sleep(2 * attempt)
    raise RuntimeError("unreachable")



def pickup_comment_already_posted(issue: dict[str, Any], *, task_id: str, release_sha: str) -> bool:
    for comment in issue.get("comments", []):
        body = str(comment.get("body", ""))
        if (
            "External proof fleet pickup update" in body
            and task_id in body
            and release_sha in body
        ):
            return True
    return False


def apply_to_github(entries: list[dict[str, Any]], *, release_sha: str) -> None:
    for entry in entries:
        task_id = str(entry["task_id"])
        issue_number = issue_number_from_url(str(entry["tracking_issue"]))
        title = render_issue_title(entry)
        issue_body = render_issue_body(entry)
        comment_body = render_pickup_comment(entry, release_sha)
        run_gh(
            ["gh", "issue", "edit", issue_number, "--title", title, "--body", issue_body],
        )
        issue = load_issue(issue_number)
        if pickup_comment_already_posted(issue, task_id=task_id, release_sha=release_sha):
            print(f"skipped {task_id} pickup comment -> issue #{issue_number}; current release pickup already posted")
            continue
        run_gh_with_body(["gh", "issue", "comment", issue_number], comment_body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-sha",
        help="PR #82 headRefOid to embed; defaults to live gh pr view 82",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="limit sync/rendering to one task id; repeat for multiple tasks",
    )
    parser.add_argument(
        "--issue-body-dir",
        type=Path,
        help="write rendered issue bodies to this directory instead of only printing a summary",
    )
    parser.add_argument(
        "--comment-body-dir",
        type=Path,
        help="write rendered pickup comments to this directory instead of only printing a summary",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply rendered issue bodies and pickup comments to GitHub",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    release_sha = args.release_sha or current_pr82_head()
    queue_payload = load_queue()
    task_ids = set(args.tasks) if args.tasks else None
    entries = select_entries(queue_payload, task_ids)

    write_rendered_files(
        entries,
        release_sha=release_sha,
        issue_body_dir=args.issue_body_dir,
        comment_body_dir=args.comment_body_dir,
    )

    if args.apply:
        apply_to_github(entries, release_sha=release_sha)
        action = "applied"
    else:
        action = "rendered"

    for entry in entries:
        issue_number = issue_number_from_url(str(entry["tracking_issue"]))
        print(f"{action} {entry['task_id']} -> issue #{issue_number} at PR #82 headRefOid {release_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
