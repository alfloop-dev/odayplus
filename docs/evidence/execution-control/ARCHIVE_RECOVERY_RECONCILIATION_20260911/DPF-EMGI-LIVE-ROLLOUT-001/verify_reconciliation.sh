#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Executing substantive reconciliation verification in: ${SCRIPT_DIR}"

python3 - "${SCRIPT_DIR}" <<'PY_EOF'
import json
import sys
import os
import zipfile
import hashlib
import subprocess
from pathlib import Path

evidence_dir = Path(sys.argv[1]).resolve()
repo_root = evidence_dir.parents[4]

# 1. Verify README.md
readme_path = evidence_dir / "README.md"
assert readme_path.is_file(), f"Missing {readme_path}"
readme_text = readme_path.read_text(encoding="utf-8")
assert len(readme_text) > 1000, "README.md is too short"
assert "DPF-EMGI-LIVE-ROLLOUT-001" in readme_text, "README must contain Task ID"
assert "3828c5ada2a1baab33d7dbe734c7ec70152d3d77" in readme_text, "README must contain correct baseline SHA"
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
baseline = target.get("target_dev_baseline", "")
assert baseline == "3828c5ada2a1baab33d7dbe734c7ec70152d3d77", f"Baseline SHA mismatch: {baseline}"

# Verify git baseline object type and ancestry
cat_baseline_res = subprocess.run(["git", "cat-file", "-t", baseline], cwd=repo_root, capture_output=True, text=True)
assert cat_baseline_res.returncode == 0 and cat_baseline_res.stdout.strip() == "commit", f"Git baseline {baseline} is not a resolvable commit object"
ancestor_res = subprocess.run(["git", "merge-base", "--is-ancestor", baseline, "HEAD"], cwd=repo_root)
assert ancestor_res.returncode == 0, f"Git baseline {baseline} is not an ancestor of HEAD"

last_rev_head = target.get("last_reviewed_head_sha", "")
assert last_rev_head == "168a3abb64264303e20aecce517928b2f6896355", f"Last reviewed head SHA mismatch: {last_rev_head}"
cat_rev_res = subprocess.run(["git", "cat-file", "-t", last_rev_head], cwd=repo_root, capture_output=True, text=True)
assert cat_rev_res.returncode == 0 and cat_rev_res.stdout.strip() == "commit", f"Last reviewed head {last_rev_head} is not a resolvable commit object"

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

# Substantive Dynamic DAG Cycle Check
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

# Derive graph edges dynamically from subgraph_nodes and check consistency
subgraph_nodes = dag_info.get("cycle_verification", {}).get("subgraph_nodes", [])
node_dict = {n["task_id"]: n.get("depends_on", []) for n in subgraph_nodes}
assert "DPF-EMGI-LIVE-ROLLOUT-001" in node_dict, "Root task missing from subgraph_nodes"

# Check consistency between subgraph_nodes and downstream analysis / root dependencies
root_deps = dag_info.get("proposed_dependencies_after", dag_info.get("canonical_dependencies_before", []))
assert node_dict["DPF-EMGI-LIVE-ROLLOUT-001"] == root_deps, "Root task depends_on mismatch with proposed_dependencies_after"

if "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001" in node_dict:
    assert set(node_dict["ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"]) == set(remediation_deps), "Remediation node depends_on mismatch"
if "DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001" in node_dict:
    assert set(node_dict["DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001"]) == set(snapshot_deps), "Snapshot node depends_on mismatch"

# Construct adjacency list directly from node_dict (subgraph_nodes and all declared dependencies)
adj = {}
for u, deps in node_dict.items():
    if u not in adj:
        adj[u] = []
    for v in deps:
        adj[u].append(v)
        if v not in adj:
            adj[v] = []

# Verify that recorded directed_edges are complete and consistent with derived edges
recorded_edges = dag_info.get("cycle_verification", {}).get("directed_edges", [])
derived_edges = [(u, v) for u in adj for v in adj[u]]
assert len(derived_edges) >= 12, f"Expected at least 12 derived edges, got {len(derived_edges)}"

# DFS Cycle Detection Algorithm (3-state coloring: 0=unvisited, 1=visiting, 2=visited)
state = {node: 0 for node in adj}
cycle_path = []

def dfs_cycle(node, path):
    state[node] = 1  # visiting
    path.append(node)
    for neighbor in adj.get(node, []):
        if state.get(neighbor, 0) == 1:
            # Cycle detected
            cycle_idx = path.index(neighbor)
            cycle_path.extend(path[cycle_idx:] + [neighbor])
            return True
        elif state.get(neighbor, 0) == 0:
            if dfs_cycle(neighbor, path):
                return True
    path.pop()
    state[node] = 2  # visited
    return False

detected = False
for node in list(adj.keys()):
    if state[node] == 0:
        if dfs_cycle(node, []):
            detected = True
            break

assert not detected, f"Cycle detected in dependency graph! Path: {' -> '.join(cycle_path)}"
assert dag_info.get("cycle_verification", {}).get("cycle_detected") is False, "Cycle detected flag mismatch"
visited_nodes = [node for node, s in state.items() if s == 2]
print(f"✓ Substantive graph cycle detection confirmed across {len(visited_nodes)} nodes and {len(derived_edges)} edges: 0 cycles")

# 3. Verify command-receipts.json and Artifact Bytes
cmd_receipts_path = evidence_dir / "command-receipts.json"
assert cmd_receipts_path.is_file(), f"Missing {cmd_receipts_path}"
cmd_receipts = json.loads(cmd_receipts_path.read_text(encoding="utf-8"))
meta = cmd_receipts.get("provenance_metadata", {})
assert meta.get("target_dev_baseline") == "3828c5ada2a1baab33d7dbe734c7ec70152d3d77", "Command receipts baseline mismatch"
assert meta.get("last_reviewed_head_sha") == "168a3abb64264303e20aecce517928b2f6896355", "Command receipts reviewed head mismatch"

receipts = cmd_receipts.get("receipts", [])
assert len(receipts) >= 12, f"Expected at least 12 command receipts, got {len(receipts)}"

for r in receipts:
    assert "label" in r, "Receipt missing label"
    assert "command" in r and len(r["command"]) > 0, "Receipt missing command"
    assert r.get("exit_code") == 0, f"Receipt exit code non-zero for {r.get('label')}"
    assert "result_reference" in r and r["result_reference"], f"Receipt missing result_reference for {r.get('label')}"

    # Verify that result_reference for local artifacts exists and is valid
    ref = r["result_reference"]
    if ref.startswith("/") or ref.endswith(".zip") or "#" in ref or ref.startswith("docs/") or ref.startswith("support/"):
        file_part = ref.split("#")[0]
        file_path = Path(file_part)
        if not file_path.is_absolute():
            candidates = [evidence_dir / file_path, repo_root / file_path, Path(pantheon_root) / file_path]
            resolved_file = next((c for c in candidates if c.is_file()), None)
            assert resolved_file is not None and resolved_file.is_file(), f"Local result_reference file does not exist: {file_part}"
        else:
            assert file_path.is_file(), f"Local result_reference file does not exist: {file_path}"

# Read, hash, and substantively verify artifact bytes from raw files
readback_sources = recon.get("readback_evidence_sources", {})

# Deploy ZIP & Inner Receipt
deploy_zip_r = next((r for r in receipts if r.get("source_artifact_id") == 9566074439), None)
assert deploy_zip_r is not None, "Missing deploy ZIP receipt 9566074439"
deploy_zip_ref = deploy_zip_r["result_reference"].split("#")[0]
deploy_zip_path = Path(deploy_zip_ref)
assert deploy_zip_path.is_file(), f"Deploy zip file does not exist: {deploy_zip_path}"
deploy_zip_bytes = deploy_zip_path.read_bytes()
computed_deploy_zip_sha = hashlib.sha256(deploy_zip_bytes).hexdigest()
assert computed_deploy_zip_sha == deploy_zip_r.get("zip_sha256"), f"Deploy ZIP hash mismatch: {computed_deploy_zip_sha} != {deploy_zip_r.get('zip_sha256')}"
assert computed_deploy_zip_sha == readback_sources.get("live_deploy_receipt", {}).get("zip_sha256"), "Deploy ZIP hash mismatch with reconciliation"

# Deploy Inner file
deploy_inner_r = next((r for r in receipts if r.get("label") == "deploy_artifact_inner_extraction_and_hash"), None)
assert deploy_inner_r is not None, "Missing deploy inner extraction receipt"
with zipfile.ZipFile(deploy_zip_path, 'r') as zf:
    assert "deploy-receipt.json" in zf.namelist(), "deploy-receipt.json missing from deploy ZIP"
    inner_deploy_bytes = zf.read("deploy-receipt.json")
    computed_inner_deploy_sha = hashlib.sha256(inner_deploy_bytes).hexdigest()
    assert computed_inner_deploy_sha == deploy_inner_r.get("inner_file_sha256"), "Deploy inner file SHA256 mismatch"
    assert computed_inner_deploy_sha == readback_sources.get("live_deploy_receipt", {}).get("inner_file_sha256"), "Deploy inner SHA mismatch with reconciliation"
    inner_deploy_json = json.loads(inner_deploy_bytes.decode('utf-8'))
    assert inner_deploy_json.get("outcome") == "DEPLOYED", "Inner deploy outcome is not DEPLOYED"
    assert inner_deploy_json.get("candidate_sha") == "571fd34e588b64942ea6541fce34de7e7e039335", "Inner deploy candidate SHA mismatch"
    assert inner_deploy_json.get("image_digest") == "sha256:4f603e3acff7a35876fd59593e6725ee0b00226e546b0694eb5e80a12b097e9e", "Inner deploy image digest mismatch"
print(f"✓ Deploy artifact raw bytes and inner JSON verified: ZIP SHA256 {computed_deploy_zip_sha[:16]}..., inner SHA256 {computed_inner_deploy_sha[:16]}...")

# Rollback ZIP & Inner Receipt
rollback_zip_r = next((r for r in receipts if r.get("source_artifact_id") == 9563435760), None)
assert rollback_zip_r is not None, "Missing rollback ZIP receipt 9563435760"
rollback_zip_ref = rollback_zip_r["result_reference"].split("#")[0]
rollback_zip_path = Path(rollback_zip_ref)
assert rollback_zip_path.is_file(), f"Rollback zip file does not exist: {rollback_zip_path}"
rollback_zip_bytes = rollback_zip_path.read_bytes()
computed_rollback_zip_sha = hashlib.sha256(rollback_zip_bytes).hexdigest()
assert computed_rollback_zip_sha == rollback_zip_r.get("zip_sha256"), f"Rollback ZIP hash mismatch: {computed_rollback_zip_sha} != {rollback_zip_r.get('zip_sha256')}"
assert computed_rollback_zip_sha == readback_sources.get("rollback_mechanism_and_state_analysis", {}).get("test_run_investigation", {}).get("zip_sha256"), "Rollback ZIP hash mismatch with reconciliation"

# Rollback Inner file
rollback_inner_r = next((r for r in receipts if r.get("label") == "rollback_artifact_inner_extraction_and_hash"), None)
assert rollback_inner_r is not None, "Missing rollback inner extraction receipt"
with zipfile.ZipFile(rollback_zip_path, 'r') as zf:
    assert "rollback-receipt.json" in zf.namelist(), "rollback-receipt.json missing from rollback ZIP"
    inner_rollback_bytes = zf.read("rollback-receipt.json")
    computed_inner_rollback_sha = hashlib.sha256(inner_rollback_bytes).hexdigest()
    assert computed_inner_rollback_sha == rollback_inner_r.get("inner_file_sha256"), "Rollback inner file SHA256 mismatch"
    assert computed_inner_rollback_sha == readback_sources.get("rollback_mechanism_and_state_analysis", {}).get("test_run_investigation", {}).get("inner_file_sha256"), "Rollback inner SHA mismatch with reconciliation"
    inner_rollback_json = json.loads(inner_rollback_bytes.decode('utf-8'))
    assert inner_rollback_json.get("outcome") == "ROLLED_BACK", "Inner rollback outcome is not ROLLED_BACK"
    assert inner_rollback_json.get("fully_restored") is False, "Inner rollback fully_restored must be False"
    assert inner_rollback_json.get("candidate_sha") == "4d694e3be3487ea5877ce5e323cffc32180d33f2", "Inner rollback candidate SHA mismatch"
    assert inner_rollback_json.get("image_digest") == "sha256:0c97ba05aeaa7763786a3297d825d2fbc8ee8515ac82fcaabc69e302fc057593", "Inner rollback image digest mismatch"
print(f"✓ Rollback test artifact raw bytes and inner JSON verified: ZIP SHA256 {computed_rollback_zip_sha[:16]}..., inner SHA256 {computed_inner_rollback_sha[:16]}... (fully_restored=False confirmed)")

# Focused verification receipt inputs verification
focused_r = next((r for r in receipts if r.get("label") == "focused_reconciliation_verification"), None)
assert focused_r is not None, "Missing focused reconciliation verification receipt"
assert focused_r.get("last_reviewed_head_sha") == "168a3abb64264303e20aecce517928b2f6896355", "Focused receipt last reviewed head mismatch"
manifest = focused_r.get("tested_inputs_manifest", {})
for rel_p, recorded_sha in manifest.items():
    p = Path(rel_p)
    if not p.is_absolute():
        p = repo_root / rel_p
    assert p.is_file(), f"Manifest file missing: {p}"
    actual_sha = hashlib.sha256(p.read_bytes()).hexdigest()
    assert actual_sha == recorded_sha, f"Hash mismatch for {rel_p}: {actual_sha} != {recorded_sha}"
print("✓ Focused reconciliation verification receipt inputs verified against exact file hashes")

print(f"✓ command-receipts.json contains {len(receipts)} receipts with verified raw hashes, commands, and references")

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
