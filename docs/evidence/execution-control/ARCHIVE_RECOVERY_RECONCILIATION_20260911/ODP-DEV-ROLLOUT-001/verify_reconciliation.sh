#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"

echo "================================================================================"
echo "ODP-DEV-ROLLOUT-001 Acceptance Reconciliation & Canonical DAG Verification"
echo "Evidence Directory: ${SCRIPT_DIR}"
echo "Repository Root:    ${REPO_ROOT}"
echo "================================================================================"

python3 - "${SCRIPT_DIR}" "${REPO_ROOT}" << 'PY_EOF'
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    evidence_dir = Path(sys.argv[1]).resolve()
    repo_root = Path(sys.argv[2]).resolve()

    readme_path = evidence_dir / "README.md"
    reconciliation_path = evidence_dir / "acceptance-reconciliation.json"
    snapshots_path = evidence_dir / "canonical-task-snapshots.json"

    assert readme_path.is_file(), f"Missing README.md at {readme_path}"
    assert reconciliation_path.is_file(), f"Missing acceptance-reconciliation.json at {reconciliation_path}"
    assert snapshots_path.is_file(), f"Missing canonical-task-snapshots.json at {snapshots_path}"

    recon_data = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    snapshots_data = json.loads(snapshots_path.read_text(encoding="utf-8"))

    # 1. Verify Basic Task Metadata & Baseline
    assert recon_data.get("task_id") == "ODP-DEV-ROLLOUT-001", "Invalid task_id"
    target_base = recon_data.get("reconciliation_target", {}).get("target_dev_baseline")
    assert target_base == "a04010cde22a0337fdda05a6fa5146f70cc699f4", f"Unexpected target_dev_baseline: {target_base}"

    # Verify git baseline object type and ancestry
    cat_res = subprocess.run(["git", "cat-file", "-t", target_base], cwd=repo_root, capture_output=True, text=True)
    assert cat_res.returncode == 0 and cat_res.stdout.strip() == "commit", f"Git baseline {target_base} is not a valid commit object"

    anc_res = subprocess.run(["git", "merge-base", "--is-ancestor", target_base, "HEAD"], cwd=repo_root)
    assert anc_res.returncode == 0, f"Git baseline {target_base} is not an ancestor of HEAD"

    assert recon_data.get("historical_delivery", {}).get("delivered_files_total") == 9, "Delivered files count mismatch"

    # 2. Verify Canonical Scoped Snapshots (17 Nodes) & Hash Consistency
    nodes_dict = snapshots_data.get("nodes", {})
    assert len(nodes_dict) == 17, f"Expected 17 canonical nodes, got {len(nodes_dict)}"

    actual_snapshots_hash = hashlib.sha256(snapshots_path.read_bytes()).hexdigest()
    expected_snapshots_hash = recon_data.get("dependency_graph_and_cycle_verification", {}).get("canonical_snapshots_reference", {}).get("sha256")
    assert actual_snapshots_hash == expected_snapshots_hash, f"Snapshots file hash mismatch: actual {actual_snapshots_hash} vs declared {expected_snapshots_hash}"

    # 3. Derive 17 DAG Edges directly from Canonical Scoped Snapshots
    derived_edges = []
    for node_id, node_info in sorted(nodes_dict.items()):
        for dep in sorted(node_info.get("depends_on", [])):
            derived_edges.append({"from": node_id, "to": dep})

    assert len(derived_edges) == 17, f"Expected 17 derived edges, got {len(derived_edges)}"

    # Verify edge set in acceptance-reconciliation.json matches derived edges
    dag_section = recon_data.get("dependency_graph_and_cycle_verification", {}).get("dag_cycle_check", {})
    recon_edges = dag_section.get("edge_set", [])
    assert recon_edges == derived_edges, "Reconciliation edge set does not match canonical derived edges"

    # 4. Verify Edge Set SHA-256 Hash
    canonical_edge_json = json.dumps(derived_edges, sort_keys=True)
    computed_edge_hash = hashlib.sha256(canonical_edge_json.encode("utf-8")).hexdigest()
    expected_edge_hash = dag_section.get("edge_set_sha256")
    assert computed_edge_hash == expected_edge_hash, f"Edge hash mismatch: {computed_edge_hash} vs {expected_edge_hash}"
    assert computed_edge_hash == "d1db3233c193f7ce93e0895efb227d3314376e68724db0bca7ae0579438fef4d", "Unexpected edge hash value"

    # 5. Full DAG Cycle Check (DFS)
    nodes = list(nodes_dict.keys())
    adj = {n: [] for n in nodes}
    for e in derived_edges:
        adj[e["from"]].append(e["to"])

    visited = {}
    cycle = False
    def dfs(u: str):
        nonlocal cycle
        visited[u] = 1
        for v in adj.get(u, []):
            if visited.get(v, 0) == 1:
                cycle = True
            elif visited.get(v, 0) == 0:
                dfs(v)
        visited[u] = 2

    for n in nodes:
        if visited.get(n, 0) == 0:
            dfs(n)

    assert cycle is False, "Cycle detected in DAG"
    assert dag_section.get("cycle_detected") is False, "cycle_detected flag must be False"

    # 6. Verify Acceptance Criteria Reconciliation Breakdown
    summary = recon_data.get("acceptance_criteria_summary", {})
    assert summary.get("total_criteria") == 5, "Total criteria must be 5"
    hist_status = summary.get("by_historical_inventory_status", {})
    assert hist_status.get("met_by_receipt") == 1, "met_by_receipt must be 1 (A1)"
    assert hist_status.get("unmet_per_own_receipt") == 2, "unmet_per_own_receipt must be 2 (A2, A5)"
    assert hist_status.get("not_evidenced") == 1, "not_evidenced must be 1 (A3)"
    assert hist_status.get("partially_met") == 1, "partially_met must be 1 (A4)"
    assert summary.get("overall_historical_candidate_verdict") == "blocked", "Overall verdict must be blocked"
    assert summary.get("disposition_conclusion") == "false_done_superseded", "Disposition must be false_done_superseded"

    # 7. Verify Remediation Mapping to ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001
    mapping_section = recon_data.get("criteria_to_remediation_task_mapping", {})
    assert mapping_section.get("successor_task_id") == "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"
    assert len(mapping_section.get("mappings", [])) == 5

    # 8. Verify PR #1109 Merge Commit Ancestry
    pr1109_merge_sha = "640e35415aa33d5d53af21a8a527431b8f751cea"
    pr1109_res = subprocess.run(["git", "merge-base", "--is-ancestor", pr1109_merge_sha, target_base], cwd=repo_root)
    assert pr1109_res.returncode == 0, f"PR #1109 merge commit {pr1109_merge_sha} is not an ancestor of {target_base}"

    print("================================================================================")
    print("ODP-DEV-ROLLOUT-001 Acceptance Reconciliation Verification: ALL CHECKS PASSED")
    print(f"- Target Baseline: {target_base} ({target_base[:12]}, verified commit & ancestor)")
    print(f"- Evaluated Nodes: {len(nodes)} canonical task nodes")
    print(f"- Derived DAG Edges: {len(derived_edges)} dependency edges")
    print(f"- Edge Set SHA-256: {computed_edge_hash}")
    print(f"- Cycle Detected: {cycle} (Strict DAG confirmed)")
    print("- Acceptance Criteria: 5/5 reconciled (Disposition: false_done_superseded)")
    print("- Successor Remediation Task: ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001")
    print(f"- PR #1109 Merge Commit Ancestry: {pr1109_merge_sha} -> {target_base} confirmed")
    print("================================================================================")
    return 0

if __name__ == "__main__":
    sys.exit(main())
PY_EOF
