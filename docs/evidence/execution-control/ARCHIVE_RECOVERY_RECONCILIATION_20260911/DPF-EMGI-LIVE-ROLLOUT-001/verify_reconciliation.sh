#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Checking evidence artifacts in: ${SCRIPT_DIR}"

python3 - <<EOF
import json
import sys
from pathlib import Path

evidence_dir = Path("${SCRIPT_DIR}").resolve()

readme_path = evidence_dir / "README.md"
assert readme_path.is_file(), f"Missing {readme_path}"
assert len(readme_path.read_text(encoding="utf-8")) > 100, "README.md is too short"
print("✓ README.md is present and non-empty")

recon_path = evidence_dir / "acceptance-reconciliation.json"
assert recon_path.is_file(), f"Missing {recon_path}"
recon = json.loads(recon_path.read_text(encoding="utf-8"))
assert recon.get("task_id") == "DPF-EMGI-LIVE-ROLLOUT-001", "Task ID mismatch"
assert recon.get("cross_repo_delivery", {}).get("pr_number") == 62, "PR number mismatch"
assert recon.get("cross_repo_delivery", {}).get("merged") is True, "PR must be merged"

criteria = recon.get("acceptance_criteria_reconciliation", [])
assert len(criteria) == 5, f"Expected 5 criteria, got {len(criteria)}"
for idx, c in enumerate(criteria, start=1):
    assert c.get("index") == idx, f"Criteria index mismatch at {idx}"
    assert c.get("status") == "met", f"Criteria {idx} status is not met: {c.get('status')}"
    assert len(c.get("evidence", "")) > 10, f"Evidence missing for criterion {idx}"
    print(f"✓ Criterion A{idx} is reconciled as met: {c.get('criterion')}")

summary = recon.get("summary", {})
assert summary.get("criteria_total") == 5, "Summary total mismatch"
assert summary.get("criteria_met") == 5, "Summary met count mismatch"
assert summary.get("criteria_unmet") == 0, "Summary unmet count mismatch"
assert summary.get("reconciliation_verdict") == "acceptance_fully_reconciled", "Verdict mismatch"
print("✓ acceptance-reconciliation.json valid and fully reconciled")

cmd_receipts_path = evidence_dir / "command-receipts.json"
assert cmd_receipts_path.is_file(), f"Missing {cmd_receipts_path}"
cmd_receipts = json.loads(cmd_receipts_path.read_text(encoding="utf-8"))
assert len(cmd_receipts.get("receipts", [])) >= 5, "Expected at least 5 command receipts"
print(f"✓ command-receipts.json contains {len(cmd_receipts.get('receipts'))} receipts")

print("\nAll reconciliation checks passed successfully!")
EOF
