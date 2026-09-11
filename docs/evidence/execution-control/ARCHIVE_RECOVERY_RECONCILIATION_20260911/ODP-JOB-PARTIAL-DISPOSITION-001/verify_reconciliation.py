#!/usr/bin/env python3
"""Offline Narrow-Scope Verification Script for ODP-JOB-PARTIAL-DISPOSITION-001 Reconciliation.

Validates:
1. Presence and non-emptiness of README.md, acceptance-reconciliation.json, command-receipts.json.
2. Structure and completeness of acceptance-reconciliation.json (all 4 criteria met).
3. Existence of original handback document (HB-SHARED001-PARTIAL-001).
4. Type separation between JobStatus and JobDeliveryState vocabularies.
5. Presence of historical merge commit (9647d673ccf2) and head commit (f8caf62e1164) in local git history.
6. Execution of set-valued requirements validator (check_requirement_members.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
EVIDENCE_DIR = Path(__file__).resolve().parent


def verify_files() -> None:
    print("[1/6] Verifying reconciliation artifact files...")
    readme = EVIDENCE_DIR / "README.md"
    reconciliation_json = EVIDENCE_DIR / "acceptance-reconciliation.json"
    command_receipts = EVIDENCE_DIR / "command-receipts.json"

    assert readme.is_file(), f"Missing README.md at {readme}"
    assert readme.stat().st_size > 0, "README.md is empty"

    assert reconciliation_json.is_file(), f"Missing acceptance-reconciliation.json at {reconciliation_json}"
    data = json.loads(reconciliation_json.read_text(encoding="utf-8"))
    assert data["task_id"] == "ODP-JOB-PARTIAL-DISPOSITION-001"
    assert data["summary"]["criteria_total"] == 4
    assert data["summary"]["criteria_met"] == 4
    assert data["summary"]["reconciliation_verdict"] == "acceptance_fully_reconciled"

    assert command_receipts.is_file(), f"Missing command-receipts.json at {command_receipts}"
    receipts_data = json.loads(command_receipts.read_text(encoding="utf-8"))
    assert len(receipts_data.get("receipts", [])) >= 4
    print("  ✓ All reconciliation artifact files present and structured correctly.")


def verify_handback_document() -> None:
    print("[2/6] Verifying Handback document HB-SHARED001-PARTIAL-001...")
    handback_path = REPO_ROOT / "docs" / "evidence" / "ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md"
    assert handback_path.is_file(), f"Missing Handback document at {handback_path}"
    content = handback_path.read_text(encoding="utf-8")
    assert "HB-SHARED001-PARTIAL-001" in content
    assert "BLOCKED_BY_EVIDENCE" in content
    assert "明細收據與成員識別架構契約" in content
    assert "重試契約（不重做成功項）" in content
    print("  ✓ Handback document verified.")


def verify_type_separation() -> None:
    print("[3/6] Verifying JobStatus and JobDeliveryState type separation...")
    sys.path.insert(0, str(REPO_ROOT))
    from shared.governance.vocabularies import JobDeliveryState, JobStatus

    outcomes = {s.value for s in JobStatus}
    deliveries = {d.value for d in JobDeliveryState}
    assert outcomes == {"queued", "running", "succeeded", "failed", "cancelled", "partial"}
    assert deliveries == {"retrying", "dead_letter"}
    assert outcomes.isdisjoint(deliveries), "JobStatus and JobDeliveryState must be disjoint"
    print("  ✓ Type separation verified.")


def verify_git_history() -> None:
    print("[4/6] Verifying historical commits in git history...")
    merge_sha = "9647d673ccf2c0f11ef565e78511099821d85c19"
    head_sha = "f8caf62e11643f9cbe59ea6b958faeab746fcac2"

    for sha in (merge_sha, head_sha):
        res = subprocess.run(
            ["git", "rev-parse", "--verify", sha],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Git commit {sha} not found in repository history"
    print("  ✓ Historical merge and head commits verified.")


def verify_governance_checker() -> None:
    print("[5/6] Running check_requirement_members validator...")
    from delivery_toolchain.governance.check_requirement_members import (
        MANIFEST_PATH,
        check,
    )

    failures, tally = check(REPO_ROOT, MANIFEST_PATH, reference_date=None)
    assert failures == [], f"check_requirement_members failed: {failures}"
    assert tally["requirements"] >= 6
    assert tally["dispositions"]["BLOCKED_BY_EVIDENCE"] >= 1
    print("  ✓ Governance validator passed cleanly.")


def verify_producer_reconciliation_presence() -> None:
    print("[6/6] Verifying downstream reconciliation alignment...")
    rec_doc = (
        REPO_ROOT
        / "docs"
        / "evidence"
        / "human-decisions"
        / "ODP-JOB-PARTIAL-PRODUCER-RECONCILIATION-001"
        / "README.md"
    )
    assert rec_doc.is_file(), f"Missing producer reconciliation doc at {rec_doc}"
    print("  ✓ Downstream producer reconciliation document verified.")


def main() -> None:
    print("==================================================================")
    print("Starting ODP-JOB-PARTIAL-DISPOSITION-001 Verification")
    print("==================================================================")
    verify_files()
    verify_handback_document()
    verify_type_separation()
    verify_git_history()
    verify_governance_checker()
    verify_producer_reconciliation_presence()
    print("==================================================================")
    print("ALL 6 VERIFICATION CHECKS PASSED (exit code 0)")
    print("==================================================================")


if __name__ == "__main__":
    main()
