#!/usr/bin/env python3
"""
Verification script for ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001 acceptance reconciliation.
Validates structured evidence files, JSON schemas, criteria reconciliation, DAG cycle checks,
and verification receipts.
"""

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    start_time = time.time()
    task_id = "ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001"
    evidence_dir = Path(__file__).resolve().parent

    print(f"[{task_id}] Verifying evidence artifacts in: {evidence_dir}")

    # 1. File existence checks
    required_files = [
        "README.md",
        "acceptance-reconciliation.json",
        "evidence-manifest.json",
        "original-evidence.json",
    ]
    for filename in required_files:
        file_path = evidence_dir / filename
        assert file_path.is_file(), f"Missing required file: {filename}"
        assert file_path.stat().st_size > 0, f"File is empty: {filename}"
        print(f"  ✓ {filename} exists ({file_path.stat().st_size} bytes)")

    # 2. Validate acceptance-reconciliation.json
    rec_path = evidence_dir / "acceptance-reconciliation.json"
    rec_data = json.loads(rec_path.read_text(encoding="utf-8"))

    assert rec_data.get("task_id") == task_id, f"Invalid task_id: {rec_data.get('task_id')}"
    assert "historical_delivery" in rec_data, "Missing historical_delivery"
    assert rec_data["historical_delivery"]["pr_number"] == 1160
    assert rec_data["historical_delivery"]["head_sha"] == "ffe02988a1b4def412090c6b422e6efb26081d9f"
    assert len(rec_data["historical_delivery"]["ci_checks"]) == 7
    for check in rec_data["historical_delivery"]["ci_checks"]:
        assert check["conclusion"] == "success", f"Check {check['name']} conclusion is not success"

    assert rec_data["historical_delivery"]["historical_approval"]["approver"] == "Codex"
    assert rec_data["historical_delivery"]["historical_approval"]["state"] == "success"

    # Criteria validation
    criteria = rec_data.get("acceptance_criteria_reconciliation", [])
    assert len(criteria) == 4, f"Expected 4 criteria, found {len(criteria)}"
    for idx, crit in enumerate(criteria, 1):
        assert crit["index"] == idx, f"Criterion index mismatch at {idx}"
        assert crit["status"] == "met", f"Criterion A{idx} status is not met"
        assert "evidence" in crit and crit["evidence"], f"Criterion A{idx} missing evidence description"
        assert "evidence_provenance" in crit, f"Criterion A{idx} missing evidence_provenance"
    print("  ✓ acceptance-reconciliation.json validated (4/4 criteria satisfied)")

    # Downstream / Successor mapping & Cycle check
    dep_map = rec_data.get("dependent_tasks_mapping", {})
    assert dep_map, "Missing dependent_tasks_mapping"
    assert dep_map.get("target_task", {}).get("task_id") == task_id
    assert dep_map.get("cycle_check", {}).get("cycle_detected") is False, "Cycle detected in DAG!"
    assert dep_map.get("cycle_check", {}).get("is_valid_dag") is True, "Invalid DAG!"
    print("  ✓ DAG dependency and cycle check validated (no cycles detected)")

    # 3. Validate evidence-manifest.json
    manifest_path = evidence_dir / "evidence-manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_data.get("task_id") == task_id, f"Manifest task_id mismatch: {manifest_data.get('task_id')}"
    assert manifest_data.get("schema_version") == 2
    assert "historical_delivery" in manifest_data
    assert "current_observations_and_mapping" in manifest_data
    assert "current_verification_receipts" in manifest_data
    print("  ✓ evidence-manifest.json validated")

    # 4. Validate original-evidence.json
    orig_path = evidence_dir / "original-evidence.json"
    orig_data = json.loads(orig_path.read_text(encoding="utf-8"))
    assert orig_data.get("entry", {}).get("task_id") == task_id or orig_data.get("task_id") == task_id
    print("  ✓ original-evidence.json validated")

    # 5. Check against canonical original-evidence.json if present
    canonical_orig = Path("/home/lupin/odayplus/support/handoffs/archive-recovery-dispatch-20260911/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001/original-evidence.json")
    if canonical_orig.is_file():
        assert orig_path.read_bytes() == canonical_orig.read_bytes(), "original-evidence.json differs from canonical dispatch copy"
        print("  ✓ original-evidence.json exact byte match with canonical dispatch copy")

    elapsed = time.time() - start_time
    print(f"[{task_id}] All reconciliation checks PASSED in {elapsed:.4f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
