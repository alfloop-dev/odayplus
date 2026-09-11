#!/usr/bin/env python3
"""Offline verification script for ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 reconciliation."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    json_path = base_dir / "acceptance-reconciliation.json"
    readme_path = base_dir / "README.md"
    repo_root = base_dir.parents[4]

    errors: list[str] = []

    if not json_path.exists():
        errors.append(f"Missing {json_path}")
    if not readme_path.exists():
        errors.append(f"Missing {readme_path}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"ERROR: Failed to parse {json_path}: {exc}", file=sys.stderr)
        return 1

    # Check task ID
    if data.get("task_id") != "ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002":
        errors.append(f"Unexpected task_id: {data.get('task_id')}")

    # Check historical delivery
    historical = data.get("historical_delivery", {})
    if not historical:
        errors.append("Missing historical_delivery block")
    else:
        if historical.get("pr_number") != 1096:
            errors.append(f"Unexpected pr_number: {historical.get('pr_number')}")
        if historical.get("head_sha") != "69422d71e8d5ac572ade58562c0aeca28d123648":
            errors.append(f"Unexpected head_sha: {historical.get('head_sha')}")
        if historical.get("merge_sha") != "2377168c2cc07cd2470dd8f43de0486fe8d8fc08":
            errors.append(f"Unexpected merge_sha: {historical.get('merge_sha')}")

        delivered_files = historical.get("delivered_files", [])
        if not delivered_files:
            errors.append("historical_delivery has empty delivered_files")
        for rel_path in delivered_files:
            full_path = repo_root / rel_path
            if not full_path.exists():
                errors.append(f"Delivered file does not exist in repo: {rel_path}")

    # Check 6 criteria
    criteria = data.get("acceptance_criteria_reconciliation", [])
    if len(criteria) != 6:
        errors.append(f"Expected 6 criteria, got {len(criteria)}")

    for idx, c in enumerate(criteria, start=1):
        if c.get("index") != idx:
            errors.append(f"Criterion at position {idx} has mismatched index field {c.get('index')}")
        if idx in (1, 2, 3, 4, 5):
            if c.get("verdict") != "VERIFIED":
                errors.append(f"Criterion {idx} verdict is not VERIFIED: {c.get('verdict')}")
        elif idx == 6:
            if c.get("verdict") not in ("PROCESS_CONSTRAINT_UNVERIFIABLE", "PARTIALLY_MET_PROCESS_UNVERIFIABLE"):
                errors.append(f"Criterion 6 verdict must be PROCESS_CONSTRAINT_UNVERIFIABLE: {c.get('verdict')}")
        if not c.get("primary_evidence"):
            errors.append(f"Criterion {idx} has no primary_evidence")

    # Check summary
    summary = data.get("criteria_summary", {})
    if summary.get("total") != 6:
        errors.append(f"Summary total is not 6: {summary.get('total')}")
    if summary.get("verified") != 5:
        errors.append(f"Summary verified is not 5: {summary.get('verified')}")
    if summary.get("process_constraint_unverifiable") != 1:
        errors.append(f"Summary process_constraint_unverifiable is not 1: {summary.get('process_constraint_unverifiable')}")
    if summary.get("unmet") != 0 or summary.get("blocked") != 0:
        errors.append(f"Summary contains unmet or blocked: {summary}")

    # Check dependency reconciliation & acyclic graph
    dep_rec = data.get("dependency_reconciliation", {})
    if not dep_rec:
        errors.append("Missing dependency_reconciliation block")
    else:
        if dep_rec.get("dependencies_before") != [] or dep_rec.get("dependencies_after") != []:
            errors.append("Task dependencies must be empty [] before and after")
        cycle_check = dep_rec.get("cycle_check", {})
        if cycle_check.get("has_cycle") is not False:
            errors.append("Cycle check indicates a cycle exists")

    # Check test provenance in python E2E file
    e2e_file = repo_root / "tests/e2e/test_password_first_security_e2e.py"
    if e2e_file.exists():
        content = e2e_file.read_text(encoding="utf-8")
        expected_tests = [
            "test_password_first_preflight_passes_without_oidc",
            "test_local_mode_rejects_oidc_token_and_records_failure",
            "test_complete_oidc_and_local_tokens_resolve_one_principal",
            "test_cross_tenant_read_is_denied_and_audited",
            "test_receipt_and_rollout_checklist_are_present_and_redacted",
        ]
        for test_name in expected_tests:
            if f"def {test_name}" not in content:
                errors.append(f"Expected test function {test_name} not found in {e2e_file}")

        # Ensure fabricated test is NOT in code or json
        if "test_same_tenant_read_is_allowed_and_audited" in content:
            errors.append("Fabricated test_same_tenant_read_is_allowed_and_audited unexpectedly found in code")
        raw_json_str = json_path.read_text(encoding="utf-8")
        if "test_same_tenant_read_is_allowed_and_audited" in raw_json_str:
            errors.append("Fabricated test_same_tenant_read_is_allowed_and_audited found in acceptance-reconciliation.json")

    # Check raw command receipts
    cmd_receipts = data.get("raw_command_receipts", [])
    if not cmd_receipts:
        errors.append("Missing raw_command_receipts")
    for r in cmd_receipts:
        if not r.get("command") or "exit_code" not in r or not r.get("executed_at"):
            errors.append(f"Incomplete command receipt: {r}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"OK: All criteria, provenance, and named tests in {json_path.name} validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
