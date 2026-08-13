#!/usr/bin/env python3
"""Aggregate live fleet-dispatch readiness for the manifest-selected release PR.

This is the release-owner view of "has the work actually been sent to fleets?"
It does not replace the specialized checkers; it runs them together and renders
one status report covering:

- release PR head and attached checks;
- external-proof issue sync/comments/blocker state for #132-#138;
- product closeout PR comment for owner/reviewer/Human-Ops lifecycle actions;
- current product closeout action matrix.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
E2E_DIR = Path(__file__).resolve().parent
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from _release_target import release_pr_number, release_pr_view_args

LIVE_CHECKS = (
    ("external issue sync", ["python3", "scripts/e2e/check_external_proof_issue_sync.py", "--require-assignees"]),
    ("external fleet notifications", ["python3", "scripts/e2e/check_external_proof_fleet_notifications.py"]),
    ("external live blockers", ["python3", "scripts/e2e/check_external_proof_live_blockers.py", "--require-assignees"]),
    ("external handback board", ["python3", "scripts/e2e/check_external_proof_handback_status_board.py"]),
    ("product closeout PR notification", ["python3", "scripts/e2e/check_product_closeout_fleet_notification.py"]),
    (
        "product closeout action matrix",
        ["python3", "scripts/e2e/check_product_closeout_action_matrix.py", "--json"],
    ),
)

READINESS_REPORT_COMMAND = ["python3", "scripts/e2e/check_external_proof_acceptance_readiness.py", "--report"]
ISSUE_HANDBACK_SCAN_COMMAND = [
    "python3",
    "scripts/e2e/check_external_proof_issue_handback_scan.py",
    "--report",
    "--fail-on-escalation",
]


def current_release_pr_payload() -> dict[str, Any]:
    raw = subprocess.check_output(
        release_pr_view_args(QUEUE_PATH, "number", "isDraft", "state", "headRefOid", "mergeStateStatus", "statusCheckRollup", "url"),
        cwd=ROOT,
        text=True,
    )
    return json.loads(raw)


def validate_release_pr_payload(pr_payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_pr = release_pr_number(QUEUE_PATH)
    label = f"PR #{expected_pr}"
    if pr_payload.get("number") != expected_pr:
        errors.append(f"release PR number must be {expected_pr}")
    if pr_payload.get("state") != "OPEN":
        errors.append(f"{label} must be OPEN")
    if pr_payload.get("mergeStateStatus") != "CLEAN":
        errors.append(f"{label} mergeStateStatus must be CLEAN")
    head = str(pr_payload.get("headRefOid", ""))
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head.lower()):
        errors.append(f"{label} headRefOid must be a full SHA")

    checks = pr_payload.get("statusCheckRollup", [])
    if not checks:
        errors.append(f"{label} must have attached checks")
    for check in checks:
        name = str(check.get("name", "<unnamed>"))
        status = check.get("status")
        conclusion = check.get("conclusion")
        if status != "COMPLETED" or conclusion != "SUCCESS":
            errors.append(f"{label} check {name!r} must be COMPLETED/SUCCESS")
    return errors


def run_check(label: str, command: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT, env=env, check=False, capture_output=True, text=True)
    output = "\n".join(line for line in (result.stdout + result.stderr).splitlines() if line.strip())
    return {"label": label, "command": " ".join(command), "returncode": result.returncode, "output": output}


def build_live_report() -> dict[str, Any]:
    pr_payload = current_release_pr_payload()
    env = os.environ.copy()
    env.setdefault("PANTHEON_STATUS_ROOT", "/home/lupin/oday-plus")
    checks = [run_check(label, command, env=env) for label, command in LIVE_CHECKS]
    readiness_report = run_check("external acceptance readiness", READINESS_REPORT_COMMAND, env=env)
    issue_handback_scan = run_check("external issue handback scan", ISSUE_HANDBACK_SCAN_COMMAND, env=env)
    return {
        "pr": pr_payload,
        "checks": checks,
        "acceptance_readiness": readiness_report,
        "issue_handback_scan": issue_handback_scan,
    }


def validate_report(report: dict[str, Any]) -> list[str]:
    errors = validate_release_pr_payload(report.get("pr", {}))
    for check in report.get("checks", []):
        if check.get("returncode") != 0:
            label = check.get("label", "<unknown>")
            output = str(check.get("output", "")).strip()
            errors.append(f"{label} failed: {output}")
    issue_handback_scan = report.get("issue_handback_scan")
    if isinstance(issue_handback_scan, dict) and issue_handback_scan.get("returncode") != 0:
        output = str(issue_handback_scan.get("output", "")).strip()
        errors.append(f"external issue handback scan failed: {output}")
    return errors


def render_markdown(report: dict[str, Any]) -> str:
    pr = report.get("pr", {})
    checks = report.get("checks", [])
    lines = [
        "# Release Fleet Dispatch Status",
        "",
        f"PR: #{pr.get('number')} {pr.get('url', '')}",
        f"Head: `{pr.get('headRefOid', '')}`",
        f"Draft: `{pr.get('isDraft')}`",
        f"State: `{pr.get('state')}`",
        f"Merge state: `{pr.get('mergeStateStatus')}`",
        "",
        "## Attached Checks",
        "",
        "| Check | Status | Conclusion |",
        "|---|---|---|",
    ]
    for check in pr.get("statusCheckRollup", []):
        lines.append(f"| {check.get('name')} | {check.get('status')} | {check.get('conclusion')} |")

    lines.extend(["", "## Fleet Dispatch Guards", "", "| Guard | Exit | Command |", "|---|---:|---|"])
    for check in checks:
        lines.append(f"| {check.get('label')} | {check.get('returncode')} | `{check.get('command')}` |")

    errors = validate_report(report)
    readiness = report.get("acceptance_readiness")
    if isinstance(readiness, dict) and readiness.get("output"):
        lines.extend(
            [
                "",
                "## External Proof Acceptance Readiness",
                "",
                str(readiness.get("output", "")).strip(),
            ]
        )
    issue_handback_scan = report.get("issue_handback_scan")
    if isinstance(issue_handback_scan, dict) and issue_handback_scan.get("output"):
        lines.extend(
            [
                "",
                "## External Issue Handback Scan",
                "",
                str(issue_handback_scan.get("output", "")).strip(),
            ]
        )

    lines.extend(["", "## Verdict", ""])
    if errors:
        lines.append("failed")
        lines.append("")
        for error in errors:
            lines.append(f"- {error}")
    else:
        lines.append("passed")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument("--report", action="store_true", help="emit Markdown report")
    parser.add_argument("--fixture", type=Path, help="validate a saved JSON report instead of live GitHub")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else build_live_report()
    errors = validate_report(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.report:
        print(render_markdown(report), end="")
    elif errors:
        print("Release fleet dispatch status checks failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Release fleet dispatch status checks passed.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
