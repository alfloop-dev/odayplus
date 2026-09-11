#!/usr/bin/env python3
"""Offline verification script for XR-EXT-OSS-FINAL-AUDIT-001 reconciliation artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    evidence_dir = Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/XR-EXT-OSS-FINAL-AUDIT-001")
    readme_path = evidence_dir / "README.md"
    reconciliation_path = evidence_dir / "acceptance-reconciliation.json"

    assert readme_path.is_file(), f"Missing README: {readme_path}"
    assert reconciliation_path.is_file(), f"Missing JSON: {reconciliation_path}"

    readme_text = readme_path.read_text(encoding="utf-8")
    assert "XR-EXT-OSS-FINAL-AUDIT-001" in readme_text, "README missing task ID"
    assert "oday-data-platform" in readme_text, "README missing corrected repository"
    assert "7b0670d7" in readme_text, "README missing merge SHA"
    assert "b1824c97" in readme_text, "README missing head SHA"
    assert "HUMAN-OSS-LEGAL-APPROVAL-001" in readme_text, "README missing dependent task"

    data = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    assert data.get("task_id") == "XR-EXT-OSS-FINAL-AUDIT-001", "Invalid task_id"
    assert data.get("phase") == "Third-party data production closeout / History Recovery", "Invalid phase"

    rep_corr = data.get("repository_correction", {})
    assert rep_corr.get("actual_delivery_repository") == "alfloop-dev/oday-data-platform", "Invalid actual repository"
    assert rep_corr.get("corrected_primary_delivery_pr", {}).get("pr_number") == 61, "Invalid PR number"
    assert rep_corr.get("corrected_primary_delivery_pr", {}).get("head_sha") == "b1824c979aca008da10aed01fbc0c0a269c581dc", "Invalid head SHA"
    assert rep_corr.get("corrected_primary_delivery_pr", {}).get("merge_commit_sha") == "7b0670d7b37e59e06bc9fea5b6003d1964be2c3c", "Invalid merge SHA"

    hist = data.get("historical_delivery", {})
    assert hist.get("merged") is True, "Expected merged=True"
    assert len(hist.get("ci_checks", [])) == 7, "Expected 7 CI checks"
    assert all(c.get("conclusion") == "success" for c in hist.get("ci_checks", [])), "All CI checks must be success"
    assert hist.get("historical_approval", {}).get("state") == "success", "Approval state must be success"
    assert hist.get("historical_approval", {}).get("approver") == "Codex", "Approver must be Codex"

    criteria = data.get("acceptance_criteria_reconciliation", [])
    assert len(criteria) == 5, f"Expected 5 criteria, got {len(criteria)}"
    assert all(c.get("status") == "met" for c in criteria), "All criteria must have status met"

    summary = data.get("summary", {})
    assert summary.get("criteria_total") == 5, "Summary total criteria mismatch"
    assert summary.get("criteria_met") == 5, "Summary met criteria mismatch"
    assert summary.get("criteria_unmet") == 0, "Summary unmet criteria mismatch"
    assert summary.get("reconciliation_verdict") == "acceptance_fully_reconciled", "Invalid verdict"

    print("XR-EXT-OSS-FINAL-AUDIT-001 reconciliation artifacts verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
