#!/usr/bin/env python3
"""Capture narrowly scoped evidence checks; no network or canonical writes."""
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[4]
TASK = EVIDENCE.name


def digest(content):
    return hashlib.sha256(content).hexdigest()


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


def topological_order(adjacency):
    state = {}
    order = []

    def visit(node):
        assert state.get(node) != 1, f"cycle at {node}"
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in sorted(adjacency.get(node, [])):
            visit(dep)
        state[node] = 2
        order.append(node)

    for node in sorted(adjacency):
        visit(node)
    return order


def check():
    data = load("acceptance-reconciliation.json")
    assert data["task_id"] == TASK
    snapshot_path = EVIDENCE / "dependency-input-snapshot.json"
    snapshot = json.loads(snapshot_path.read_bytes())
    result = {
        "input_path": str(snapshot_path.relative_to(REPO)),
        "input_sha256": digest(snapshot_path.read_bytes()),
        "input_bytes": snapshot_path.stat().st_size,
        "observation_scope": "Current execution over the preserved historical input; no fresh canonical source read.",
    }
    if TASK.startswith("ODP-SITE001"):
        manifest = load("evidence-manifest.json")
        assert data["dependent_tasks_mapping"]["cycle_check"]["dependency_input_snapshot"] == snapshot
        found = []

        def find_snapshots(value):
            if isinstance(value, dict):
                if "dependency_input_snapshot" in value:
                    found.append(value["dependency_input_snapshot"])
                for child in value.values():
                    find_snapshots(child)
            elif isinstance(value, list):
                for child in value:
                    find_snapshots(child)

        find_snapshots(manifest)
        assert len(found) == 1 and found[0] == snapshot
        assert data["current_verification_receipts"] == manifest["current_verification_receipts"]
        adjacency = {node: entry["depends_on"] for node, entry in snapshot.items()}
        assert all(entry["task_id"] == node and len(entry["source_sha256"]) == 64 for node, entry in snapshot.items())
        assert all(dep in adjacency for deps in adjacency.values() for dep in deps)
        assert len(adjacency) == 49 and sum(map(len, adjacency.values())) == 51
        assert data["summary"]["criteria_met"] == 4
        result["topological_order"] = topological_order(adjacency)
        assert len(result["topological_order"]) == 49
    else:
        assert snapshot == data["canonical_task_dag_snapshot"]
        adjacency = snapshot["tasks"]
        adjacency_hash = digest(json.dumps(adjacency, sort_keys=True, indent=2).encode())
        assert adjacency_hash == snapshot["adjacency_sha256"] == "592b8e0f8285d6879790c2047805be54347069022fdea24b2fa9f0b6c9147390"
        assert len(adjacency) == 30
        assert len(data["acceptance_criteria_reconciliation"]) == 5
        assert data["summary"]["criteria_met"] == 2
        assert data["summary"]["criteria_partially_met"] == 3
        assert data["summary"]["can_closeout_as_done"] is False
        assert data["summary"]["human_oss_legal_dependency_unblocked_technically"] is False
        digests = data["this_round_observation_and_provenance"]["full_sha256_digests"]
        assert len(digests) == 11 and all(len(value.split("sha256:")[-1]) == 64 for value in digests.values())
        network = data["this_round_observation_and_provenance"]["egress_network_policy"]
        assert "199.36.153.4/30" in network["allowed_cidrs"] and network["default_deny"] is True
        assert TASK in adjacency["HUMAN-OSS-LEGAL-APPROVAL-001"]
        result["topological_order"] = topological_order(adjacency)
        proposed = dict(adjacency)
        proposed["HUMAN-OSS-LEGAL-APPROVAL-001"] = [TASK, "DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001", "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"]
        result["proposed_topological_order"] = topological_order(proposed)
        result["adjacency_sha256"] = adjacency_hash

        # Fresh 2026-09-20 live Canonical DAG verification
        fresh_path = EVIDENCE / "requery-20260920/fresh-canonical-dag.stdout.json"
        fresh_data = json.loads(fresh_path.read_bytes())
        assert data["fresh_canonical_dag_snapshot_20260920"]["tasks"] == fresh_data["adjacency"]
        assert data["fresh_canonical_dag_snapshot_20260920"]["adjacency_sha256"] == fresh_data["adjacency_sha256"] == "7ab7e86e8578e237656c4d69150092b3e44db436d2e98c6f186805e07ecfcb38"
        assert len(fresh_data["adjacency"]) == 24
        fresh_adj = {k: v["depends_on"] for k, v in fresh_data["adjacency"].items()}
        result["fresh_canonical_topological_order"] = topological_order(fresh_adj)
        assert len(result["fresh_canonical_topological_order"]) == 64
        fresh_proposed = dict(fresh_adj)
        fresh_proposed["HUMAN-OSS-LEGAL-APPROVAL-001"] = sorted(list(set(fresh_adj.get("HUMAN-OSS-LEGAL-APPROVAL-001", []) + ["DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001", "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"])))
        result["fresh_proposed_topological_order"] = topological_order(fresh_proposed)
        assert len(result["fresh_proposed_topological_order"]) == 64
        result["fresh_adjacency_sha256"] = fresh_data["adjacency_sha256"]
    result.update(active_records=len(adjacency), edges=sum(map(len, adjacency.values())), cycles=0)
    print(json.dumps(result, indent=2, sort_keys=True))


def capture():
    output = EVIDENCE / "verification-20260913"
    output.mkdir(exist_ok=False)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=REPO, text=True).strip()
    inputs = {}
    for path in sorted(EVIDENCE.rglob("*")):
        if path.is_file() and output not in path.parents:
            content = path.read_bytes()
            inputs[str(path.relative_to(REPO))] = {"sha256": digest(content), "bytes": len(content)}
    base = "a04010cde22a0337fdda05a6fa5146f70cc699f4" if TASK == "ODP-DEV-ROLLOUT-001" else "3828c5ada2a1baab33d7dbe734c7ec70152d3d77"
    commands = [["git", "diff", "--check", base, "--", str(EVIDENCE.relative_to(REPO))]]
    if TASK == "ODP-DEV-ROLLOUT-001":
        commands.append(["bash", str(EVIDENCE / "verify_reconciliation.sh")])
    else:
        commands.append([sys.executable, str(Path(__file__).resolve()), "--check"])
    if TASK.startswith("ODP-SITE001"):
        canonical_copy = Path("/home/lupin/odayplus/support/handoffs/archive-recovery-dispatch-20260911") / TASK / "original-evidence.json"
        inputs[str(canonical_copy)] = {"sha256": digest(canonical_copy.read_bytes()), "bytes": canonical_copy.stat().st_size}
        commands.append(["cmp", "-s", str(EVIDENCE / "original-evidence.json"), str(canonical_copy)])
    receipt = {
        "task_id": TASK,
        "actor": "Codex contributor",
        "repository": "alfloop-dev/odayplus",
        "cwd": str(REPO),
        "measured_head_sha": head,
        "measured_head_tree": tree,
        "revision_semantics": "The parent HEAD identifies repository ancestry. Changed evidence is bound by actual worktree input hashes, not claimed committed at HEAD.",
        "input_files": inputs,
        "retry_reason": "Required narrow capture after correcting unretained or mixed-revision raw receipts; no historical product suites rerun.",
        "commands": [],
    }
    for index, argv in enumerate(commands, 1):
        started = datetime.now(UTC).isoformat()
        monotonic_start = time.monotonic_ns()
        process = subprocess.run(argv, cwd=REPO, capture_output=True, check=False)
        elapsed = time.monotonic_ns() - monotonic_start
        finished = datetime.now(UTC).isoformat()
        entry = {"argv": argv, "started_at": started, "completed_at": finished, "duration_seconds": elapsed / 1e9, "duration_source": "time.monotonic_ns around subprocess.run", "exit_code": process.returncode}
        for stream, content in [("stdout", process.stdout), ("stderr", process.stderr)]:
            destination = output / f"command-{index}.{stream}.txt"
            destination.write_bytes(content)
            entry[stream] = {"raw_result_ref": str(destination.relative_to(EVIDENCE)), "sha256": digest(content), "bytes": len(content)}
        receipt["commands"].append(entry)
    receipt["inputs_unchanged_after_execution"] = all(digest((REPO / path).read_bytes()) == metadata["sha256"] for path, metadata in inputs.items())
    receipt["all_commands_succeeded"] = all(entry["exit_code"] == 0 for entry in receipt["commands"])
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"receipt": str(output / "receipt.json"), "head": head, "inputs": len(inputs), "exit_codes": [entry["exit_code"] for entry in receipt["commands"]], "inputs_unchanged": receipt["inputs_unchanged_after_execution"]}))
    return 0 if receipt["all_commands_succeeded"] and receipt["inputs_unchanged_after_execution"] else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        check()
    elif sys.argv[1:]:
        raise SystemExit("usage: capture_verification.py [--check]")
    else:
        raise SystemExit(capture())
