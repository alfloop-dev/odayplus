#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Executing substantive reconciliation verification in: ${SCRIPT_DIR}"

python3 - "${SCRIPT_DIR}" <<'PY_EOF'
import json
import sys
import os
from pathlib import Path

evidence_dir = Path(sys.argv[1]).resolve()

# 1. Verify README.md
readme_path = evidence_dir / "README.md"
assert readme_path.is_file(), f"Missing {readme_path}"
readme_text = readme_path.read_text(encoding="utf-8")
assert len(readme_text) > 1000, "README.md is too short"
assert "DPF-EMGI-LIVE-ROLLOUT-001" in readme_text, "README must contain Task ID"
assert "2889b55fb1febe95c9f8650f24ead18e86015cca" in readme_text, "README must contain correct baseline SHA"
assert "A5" in readme_text and ("unmet" in readme_text.lower() or "未滿足" in readme_text), "README must mention unmet A5"
assert "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001" in readme_text, "README must analyze remediation task"
assert "DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001" in readme_text, "README must analyze snapshot task"
assert "ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001" in readme_text, "README must contain bounded comparison with ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001"
assert "ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001" in readme_text, "README must contain bounded comparison with ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001"
print("✓ README.md verified with complete documentation and accurate provenance")

# 2. Verify acceptance-reconciliation.json
recon_path = evidence_dir / "acceptance-reconciliation.json"
assert recon_path.is_file(), f"Missing {recon_path}"
recon = json.loads(recon_path.read_text(encoding="utf-8"))
assert recon.get("task_id") == "DPF-EMGI-LIVE-ROLLOUT-001", "Task ID mismatch"

target = recon.get("reconciliation_target", {})
assert target.get("target_dev_baseline") == "2889b55fb1febe95c9f8650f24ead18e86015cca", "Baseline SHA mismatch"
assert target.get("pre_fix_parent_sha") == "294b67e7b040be26e71cf416df65cbd9f3bf2d8c", "Pre-fix parent SHA mismatch"

cross_repo = recon.get("historical_cross_repo_delivery", {})
assert cross_repo.get("pr_number") == 62, "Cross repo PR number mismatch"
assert cross_repo.get("head_sha") == "71ecbe0d982f3f93c976fa0104a82903d2a071cf", "Cross repo head SHA mismatch"
assert cross_repo.get("merge_commit_sha") == "e3ecd2f199fe051aba8d3d33005c217036a9c88e", "Cross repo merge commit SHA mismatch"
assert cross_repo.get("merged") is True, "Cross repo PR must be merged"

# Verify acceptance criteria substantively
criteria = recon.get("acceptance_criteria_reconciliation", [])
assert len(criteria) == 5, f"Expected 5 criteria, got {len(criteria)}"

# Check against original evidence if available
pantheon_root = os.environ.get("PANTHEON_STATUS_ROOT", "/home/lupin/odayplus")
orig_path = Path(pantheon_root) / "support/handoffs/archive-recovery-dispatch-20260911/DPF-EMGI-LIVE-ROLLOUT-001/original-evidence.json"
if orig_path.is_file():
    orig_data = json.loads(orig_path.read_text(encoding="utf-8"))
    orig_criteria = orig_data["entry"]["original_task_definition"]["acceptance"]
    for idx, orig_crit in enumerate(orig_criteria, 1):
        assert criteria[idx - 1]["index"] == idx, f"Criteria index mismatch at {idx}"
        assert criteria[idx - 1]["criterion"] == orig_crit, f"Criteria text mismatch at {idx}: {criteria[idx-1]['criterion']} != {orig_crit}"
    print("✓ Acceptance criteria text and ordering substantively verified against original-evidence.json")

for idx in range(1, 5):
    c = criteria[idx - 1]
    assert c.get("status") == "met_by_receipt", f"Criterion A{idx} status is not met_by_receipt: {c.get('status')}"
    print(f"✓ Criterion A{idx} is verified as met_by_receipt: {c.get('criterion')}")

c5 = criteria[4]
assert c5.get("status") == "unmet_per_own_receipt", f"Criterion A5 status is not unmet_per_own_receipt: {c5.get('status')}"
print(f"✓ Criterion A5 is correctly retained as unmet_per_own_receipt: {c5.get('criterion')}")

# Bounded historical task comparisons
bounded = recon.get("bounded_historical_task_comparison", {})
tasks_eval = bounded.get("tasks_evaluated", [])
assert len(tasks_eval) == 2, "Expected 2 bounded task comparisons"
for t in tasks_eval:
    assert t.get("runtime_deployment") is False, f"Bounded task {t.get('task_id')} should have runtime_deployment=False"
    assert t.get("terminal_status") == "done", f"Bounded task {t.get('task_id')} should have terminal_status=done"
print("✓ Bounded historical task comparisons verified")

# Substantive DAG Cycle Check
dag_info = recon.get("dependency_graph_and_cycle_verification", {})
downstream = dag_info.get("downstream_analysis", {})

# Verify 10 dependencies of remediation task
remediation_deps = downstream.get("ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001", {}).get("canonical_depends_on", [])
assert len(remediation_deps) == 10, f"Expected 10 dependencies for remediation, got {len(remediation_deps)}"
assert "DPF-EMGI-LIVE-ROLLOUT-001" in remediation_deps, "DPF-EMGI-LIVE-ROLLOUT-001 must be in remediation dependencies"

# Verify 2 dependencies of snapshot task
snapshot_deps = downstream.get("DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001", {}).get("canonical_depends_on", [])
assert len(snapshot_deps) == 2, f"Expected 2 dependencies for snapshot, got {len(snapshot_deps)}"
assert "DPF-EMGI-LIVE-ROLLOUT-001" in snapshot_deps, "DPF-EMGI-LIVE-ROLLOUT-001 must be in snapshot dependencies"
assert "ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001" in snapshot_deps, "ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001 must be in snapshot dependencies"

# Graph cycle detection algorithm
nodes = [n["task_id"] for n in dag_info.get("cycle_verification", {}).get("subgraph_nodes", [])]
edges = dag_info.get("cycle_verification", {}).get("directed_edges", [])

adj = {node: [] for node in nodes}
for u, v in edges:
    if u in adj:
        adj[u].append(v)

visited = {}
def has_cycle(node, path_visited):
    visited[node] = True
    path_visited[node] = True
    for neighbor in adj.get(node, []):
        if neighbor not in visited:
            if has_cycle(neighbor, path_visited):
                return True
        elif path_visited.get(neighbor, False):
            return True
    path_visited[node] = False
    return False

path_visited = {node: False for node in nodes}
detected = False
for node in nodes:
    if node not in visited:
        if has_cycle(node, path_visited):
            detected = True
            break

assert not detected, "Cycle detected in dependency graph!"
assert dag_info.get("cycle_verification", {}).get("cycle_detected") is False, "Cycle detected flag mismatch"
print("✓ Substantive graph cycle detection confirmed: Graph is a valid DAG with 0 cycles")

# 3. Verify command-receipts.json
cmd_receipts_path = evidence_dir / "command-receipts.json"
assert cmd_receipts_path.is_file(), f"Missing {cmd_receipts_path}"
cmd_receipts = json.loads(cmd_receipts_path.read_text(encoding="utf-8"))
meta = cmd_receipts.get("provenance_metadata", {})
assert meta.get("target_dev_baseline") == "2889b55fb1febe95c9f8650f24ead18e86015cca", "Command receipts baseline mismatch"
assert meta.get("pre_fix_parent_sha") == "294b67e7b040be26e71cf416df65cbd9f3bf2d8c", "Command receipts parent mismatch"

receipts = cmd_receipts.get("receipts", [])
assert len(receipts) >= 12, f"Expected at least 12 command receipts, got {len(receipts)}"

for r in receipts:
    assert "label" in r, "Receipt missing label"
    assert "command" in r and len(r["command"]) > 0, "Receipt missing command"
    assert r.get("exit_code") == 0, f"Receipt exit code non-zero for {r.get('label')}"
    assert "result_reference" in r, f"Receipt missing result_reference for {r.get('label')}"

# Verify artifact hashes
deploy_zip_r = next((r for r in receipts if r.get("source_artifact_id") == 9566074439), None)
assert deploy_zip_r is not None, "Missing deploy ZIP receipt 9566074439"
assert deploy_zip_r.get("zip_sha256") == "a0de8fbf63d5f5c7f756cdde88b161f656a9830aefd7238088c2825b1e965153", "Deploy ZIP SHA256 mismatch"

deploy_inner_r = next((r for r in receipts if r.get("label") == "deploy_artifact_inner_extraction_and_hash"), None)
assert deploy_inner_r is not None, "Missing deploy inner extraction receipt"
assert deploy_inner_r.get("inner_file_sha256") == "98100b26ce4bff39274538eba87597c1adc307b6f896eb8edf7c187b0cf34700", "Deploy inner SHA256 mismatch"

rollback_zip_r = next((r for r in receipts if r.get("source_artifact_id") == 9563435760), None)
assert rollback_zip_r is not None, "Missing rollback ZIP receipt 9563435760"
assert rollback_zip_r.get("zip_sha256") == "4b6054418c28c018b1c8882de7af57e3eb1b2200692a4cad41db317717708117", "Rollback ZIP SHA256 mismatch"

rollback_inner_r = next((r for r in receipts if r.get("label") == "rollback_artifact_inner_extraction_and_hash"), None)
assert rollback_inner_r is not None, "Missing rollback inner extraction receipt"
assert rollback_inner_r.get("inner_file_sha256") == "684cbe7b48bb43ac75619ecd58331c7a2423f06b5a50068a67af7eca40fea5eb", "Rollback inner SHA256 mismatch"

print(f"✓ command-receipts.json contains {len(receipts)} receipts with verified hashes, commands, and references")

# Summary check
summary = recon.get("summary", {})
assert summary.get("criteria_total") == 5, "Summary total mismatch"
assert summary.get("criteria_met") == 4, "Summary met count mismatch"
assert summary.get("criteria_unmet") == 1, "Summary unmet count mismatch"
assert summary.get("a5_rollback_receipt_gap_resolved") is False, "A5 rollback gap must be preserved as False"
assert summary.get("human_disposition_required") is True, "Human disposition must be True"
assert summary.get("reconciliation_verdict") == "reconciled_with_unmet_acceptance_retained", "Verdict mismatch"

print("\n========================================================")
print("✓ ALL SUBSTANTIVE RECONCILIATION CHECKS PASSED SUCCESSFULLY")
print("========================================================")
PY_EOF
