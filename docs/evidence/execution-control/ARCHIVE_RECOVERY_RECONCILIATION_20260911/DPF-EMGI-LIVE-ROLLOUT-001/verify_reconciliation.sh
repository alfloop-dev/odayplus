#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Checking evidence artifacts in: ${SCRIPT_DIR}"

python3 - "${SCRIPT_DIR}" <<'PY_EOF'
import json
import sys
from pathlib import Path

evidence_dir = Path(sys.argv[1]).resolve()

readme_path = evidence_dir / "README.md"
assert readme_path.is_file(), f"Missing {readme_path}"
readme_text = readme_path.read_text(encoding="utf-8")
assert len(readme_text) > 500, "README.md is too short"
assert "A5" in readme_text and ("unmet" in readme_text.lower() or "未滿足" in readme_text), "README must mention unmet A5"
print("✓ README.md is present and contains comprehensive evidence analysis")

recon_path = evidence_dir / "acceptance-reconciliation.json"
assert recon_path.is_file(), f"Missing {recon_path}"
recon = json.loads(recon_path.read_text(encoding="utf-8"))
assert recon.get("task_id") == "DPF-EMGI-LIVE-ROLLOUT-001", "Task ID mismatch"
assert recon.get("historical_cross_repo_delivery", {}).get("pr_number") == 62, "PR number mismatch"
assert recon.get("historical_cross_repo_delivery", {}).get("merged") is True, "PR must be merged"

criteria = recon.get("acceptance_criteria_reconciliation", [])
assert len(criteria) == 5, f"Expected 5 criteria, got {len(criteria)}"
for idx in range(1, 5):
    c = criteria[idx - 1]
    assert c.get("index") == idx, f"Criteria index mismatch at {idx}"
    assert c.get("status") == "met_by_receipt", f"Criteria {idx} status is not met_by_receipt: {c.get('status')}"
    print(f"✓ Criterion A{idx} is verified as met_by_receipt: {c.get('criterion')}")

c5 = criteria[4]
assert c5.get("index") == 5, "Criteria 5 index mismatch"
assert c5.get("status") == "unmet_per_own_receipt", f"Criteria 5 status is not unmet_per_own_receipt: {c5.get('status')}"
print(f"✓ Criterion A5 is correctly retained as unmet_per_own_receipt: {c5.get('criterion')}")

summary = recon.get("summary", {})
assert summary.get("criteria_total") == 5, "Summary total mismatch"
assert summary.get("criteria_met") == 4, "Summary met count mismatch"
assert summary.get("criteria_unmet") == 1, "Summary unmet count mismatch"
assert summary.get("a5_rollback_receipt_gap_resolved") is False, "A5 rollback gap must be preserved as False"
assert summary.get("human_disposition_required") is True, "Human disposition must be True"
assert summary.get("reconciliation_verdict") == "reconciled_with_unmet_acceptance_retained", "Verdict mismatch"
print("✓ acceptance-reconciliation.json valid and properly tracks unmet acceptance")

dag_info = recon.get("dependency_graph_and_cycle_verification", {})
assert dag_info.get("canonical_dependencies_before") == [], "Canonical dependencies must be empty"
assert dag_info.get("cycle_verification", {}).get("cycle_detected") is False, "Cycle must be false"
assert dag_info.get("cycle_verification", {}).get("verdict") == "valid_canonical_dag_no_cycles", "DAG verdict mismatch"
print("✓ DAG cycle verification confirmed")

cmd_receipts_path = evidence_dir / "command-receipts.json"
assert cmd_receipts_path.is_file(), f"Missing {cmd_receipts_path}"
cmd_receipts = json.loads(cmd_receipts_path.read_text(encoding="utf-8"))
receipts = cmd_receipts.get("receipts", [])
assert len(receipts) >= 10, f"Expected at least 10 command receipts, got {len(receipts)}"

# Verify artifact hashes recorded in receipts
deploy_r = next((r for r in receipts if r.get("source_artifact_id") == 9566074439), None)
assert deploy_r is not None, "Missing deploy artifact receipt 9566074439"
assert deploy_r.get("zip_sha256") == "a0de8fbf63d5f5c7f756cdde88b161f656a9830aefd7238088c2825b1e965153", "Deploy zip SHA256 mismatch"
assert deploy_r.get("inner_file_sha256") == "98100b26ce4bff39274538eba87597c1adc307b6f896eb8edf7c187b0cf34700", "Deploy inner SHA256 mismatch"

rollback_r = next((r for r in receipts if r.get("source_artifact_id") == 9563435760), None)
assert rollback_r is not None, "Missing rollback artifact receipt 9563435760"
assert rollback_r.get("zip_sha256") == "4b6054418c28c018b1c8882de7af57e3eb1b2200692a4cad41db317717708117", "Rollback zip SHA256 mismatch"
assert rollback_r.get("inner_file_sha256") == "684cbe7b48bb43ac75619ecd58331c7a2423f06b5a50068a67af7eca40fea5eb", "Rollback inner SHA256 mismatch"
print(f"✓ command-receipts.json contains {len(receipts)} receipts with exact verified artifact hashes")

print("All reconciliation checks passed successfully!")
PY_EOF
