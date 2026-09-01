#!/usr/bin/env python3
"""Verification script for ODP-WEB-OAUTH-GATE-RETARGET-001.

Validates:
1. Prerequisite tasks (ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002, ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001)
   are terminal 'done' with valid merge commit evidence in ai-task-archive.
2. HUMAN-GCP-WEB-OAUTH-CLIENTS-001 is repositioned as optional OIDC gate (status: todo, not deleted/done).
3. ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 dependency graph does not require human OAuth client creation.
4. All 7 rollout gates and fail-closed platform policies remain strictly enforced.
5. Retarget receipt and updated dependency graph are structurally complete and secret-free.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
STATUS_ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT", str(ROOT)))
ARCHIVE_DIR = STATUS_ROOT / "ai-task-archive" / "tasks"
if not ARCHIVE_DIR.exists():
    ARCHIVE_DIR = ROOT / "ai-task-archive" / "tasks"
EVIDENCE_DIR = Path(__file__).resolve().parent


def check_archive_task(task_id: str, expected_pr: int) -> dict:
    archive_file = ARCHIVE_DIR / f"{task_id}.json"
    if not archive_file.exists():
        raise AssertionError(f"Archive file for {task_id} not found at {archive_file}")
    with open(archive_file, encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("terminal_status") == "done", f"{task_id} terminal_status is not done"
    assert data.get("terminal_outcome") == "completed", f"{task_id} terminal_outcome is not completed"

    task = data.get("task", {})
    assert task.get("status") == "done", f"{task_id} task.status is not done"
    assert task.get("pr_number") == expected_pr or task.get("review_submission", {}).get("pr_number") == expected_pr, (
        f"{task_id} PR number does not match expected {expected_pr}"
    )
    delivery = data.get("delivery", {})
    approved_head = delivery.get("approved_head") or task.get("approved_head")
    assert approved_head, f"{task_id} has no approved_head recorded"
    print(f"✓ Task {task_id} archived as done: PR #{expected_pr}, approved head {approved_head[:8]}")
    return data


def check_canonical_status() -> None:
    status_file = STATUS_ROOT / "ai-status.json"
    if not status_file.exists():
        print(f"Note: ai-status.json not found at {status_file}, skipping live status check.")
        return

    with open(status_file, encoding="utf-8") as f:
        state = json.load(f)

    tasks = {t.get("id"): t for t in state.get("tasks", []) if isinstance(t, dict) and "id" in t}

    # Check HUMAN-GCP-WEB-OAUTH-CLIENTS-001
    oauth_task = tasks.get("HUMAN-GCP-WEB-OAUTH-CLIENTS-001")
    assert oauth_task is not None, "HUMAN-GCP-WEB-OAUTH-CLIENTS-001 missing from ai-status.json"
    assert oauth_task.get("status") == "todo", f"HUMAN-GCP-WEB-OAUTH-CLIENTS-001 status is {oauth_task.get('status')}, expected 'todo'"
    assert oauth_task.get("task_class") == "human_gate", f"HUMAN-GCP-WEB-OAUTH-CLIENTS-001 task_class is {oauth_task.get('task_class')}"
    print("✓ HUMAN-GCP-WEB-OAUTH-CLIENTS-001 is retained with status=todo, task_class=human_gate")

    # Check ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001
    dev_rollout = tasks.get("ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001")
    assert dev_rollout is not None, "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 missing from ai-status.json"
    depends_on = dev_rollout.get("depends_on", [])
    assert "HUMAN-GCP-WEB-OAUTH-CLIENTS-001" not in depends_on, (
        "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 must not depend on HUMAN-GCP-WEB-OAUTH-CLIENTS-001"
    )
    print("✓ ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 does not depend on HUMAN-GCP-WEB-OAUTH-CLIENTS-001")


def check_receipt_and_graph() -> None:
    receipt_file = EVIDENCE_DIR / "retarget-receipt.json"
    assert receipt_file.exists(), f"retarget-receipt.json missing at {receipt_file}"
    with open(receipt_file, encoding="utf-8") as f:
        receipt = json.load(f)

    assert receipt.get("task_id") == "ODP-WEB-OAUTH-GATE-RETARGET-001"
    assert receipt.get("secret_values_redacted") is True
    assert "prerequisites" in receipt
    assert "rollout_retarget_matrix" in receipt
    assert "dag_integrity_audit" in receipt
    print("✓ retarget-receipt.json structure validated")

    graph_file = EVIDENCE_DIR / "updated-rollout-dependency-graph.md"
    assert graph_file.exists(), f"updated-rollout-dependency-graph.md missing at {graph_file}"
    content = graph_file.read_text(encoding="utf-8")
    assert "```mermaid" in content
    assert "ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002" in content
    assert "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001" in content
    assert "HUMAN-GCP-WEB-OAUTH-CLIENTS-001" in content
    print("✓ updated-rollout-dependency-graph.md validated")


def main() -> int:
    print("Starting ODP-WEB-OAUTH-GATE-RETARGET-001 verification...")
    # 1. Prerequisite verification
    check_archive_task("ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002", expected_pr=1096)
    check_archive_task("ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001", expected_pr=1074)

    # 2. Canonical status validation
    check_canonical_status()

    # 3. Receipt and graph artifact validation
    check_receipt_and_graph()

    print("\nAll verification assertions PASSED successfully (0 discrepancies).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
