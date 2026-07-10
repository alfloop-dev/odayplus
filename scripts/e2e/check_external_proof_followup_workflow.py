#!/usr/bin/env python3
"""Check the hosted external-proof follow-up workflow.

The workflow file can be reviewed in a release PR before GitHub exposes it as
an active workflow on the default branch. The default mode validates the local
workflow contract. `--require-live-active` additionally verifies that GitHub
Actions lists the workflow as active, which should only pass after the workflow
has reached the repository default branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/external-proof-followup.yml"
WORKFLOW_NAME = "External Proof Follow-up"

REQUIRED_WORKFLOW_TOKENS = (
    "External Proof Follow-up",
    "workflow_dispatch",
    "schedule:",
    "GH_TOKEN",
    "issues: write",
    "pull-requests: read",
    "gh pr view 82",
    "check_external_proof_issue_sync.py --require-assignees",
    "check_external_proof_fleet_notifications.py",
    "check_external_proof_live_blockers.py --require-assignees",
    "check_external_proof_handback_status_board.py",
    "check_external_proof_issue_handback_scan.py",
    "--fail-on-escalation",
    "sync_external_proof_escalation_comments.py",
    "actions/upload-artifact@v4",
    "external-proof-followup",
)


def validate_local_workflow(path: Path = WORKFLOW_PATH) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing workflow file: {path.relative_to(ROOT)}"]
    text = path.read_text(encoding="utf-8")
    for token in REQUIRED_WORKFLOW_TOKENS:
        if token not in text:
            errors.append(f"workflow missing token: {token}")
    return errors


def load_live_workflows() -> list[dict]:
    raw = subprocess.check_output(
        ["gh", "workflow", "list", "--json", "name,state,path"],
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def validate_live_workflow(workflows: list[dict]) -> list[str]:
    for workflow in workflows:
        if workflow.get("name") == WORKFLOW_NAME:
            state = workflow.get("state")
            path = workflow.get("path")
            if state != "active":
                return [f"{WORKFLOW_NAME} workflow state must be active; got {state!r}"]
            if path != ".github/workflows/external-proof-followup.yml":
                return [f"{WORKFLOW_NAME} workflow path mismatch: {path!r}"]
            return []
    return [f"{WORKFLOW_NAME} workflow is not listed by GitHub Actions; merge it to the default branch before claiming hosted follow-up is active"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-live-active",
        action="store_true",
        help="also require GitHub Actions to list the workflow as active",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_local_workflow()
    live_errors: list[str] = []
    live_workflows: list[dict] | None = None
    if args.require_live_active:
        live_workflows = load_live_workflows()
        live_errors = validate_live_workflow(live_workflows)
        errors.extend(live_errors)

    if args.json:
        payload = {
            "workflow_path": str(WORKFLOW_PATH.relative_to(ROOT)),
            "local_ok": not validate_local_workflow(),
            "live_checked": args.require_live_active,
            "live_ok": args.require_live_active and not live_errors,
            "errors": errors,
            "live_workflows": live_workflows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif errors:
        print("External proof follow-up workflow checks failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("External proof follow-up workflow checks passed.")
        if not args.require_live_active:
            print("Live activation not checked; run with --require-live-active after merge to default.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
