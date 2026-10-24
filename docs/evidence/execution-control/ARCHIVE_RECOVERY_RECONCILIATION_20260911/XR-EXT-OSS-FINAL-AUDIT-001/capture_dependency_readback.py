#!/usr/bin/env python3
"""Read-only capture of the canonical state of this task's handover dependencies.

Usage: capture_dependency_readback.py <status_root> <out_dir>

Runs one child readback (``--readback``) and stores its complete stdout/stderr,
argv, wall-clock times and terminal exit code next to a receipt. The readback
only reads the live canonical status root and ``origin/dev`` blobs; it writes
nothing outside <out_dir> and performs no network or canonical writes.
"""
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[4]
TASK = EVIDENCE.name
TASK_IDS = [
    "XR-EXT-OSS-FINAL-AUDIT-001",
    "HUMAN-OSS-LEGAL-APPROVAL-001",
    "DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001",
    "DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001",
    "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001",
    "ODP-DEV-LIVE-DEPLOY-EXECUTION-001",
]
TASK_FIELDS = ["status", "owner", "reviewer", "depends_on", "pr_url", "last_update", "next"]
ROLLOUT_README = "docs/evidence/runtime/ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001/README.md"


def digest(content):
    return hashlib.sha256(content).hexdigest()


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def readback(status_root):
    status_path = status_root / "ai-status.json"
    status_bytes = status_path.read_bytes()
    tasks = json.loads(status_bytes)["tasks"]
    tasks = tasks if isinstance(tasks, list) else list(tasks.values())
    active = {task["id"]: task for task in tasks}
    result = {
        "task_id": TASK,
        "observed_at": datetime.now(UTC).isoformat(),
        "status_root": str(status_root),
        "ai_status_sha256": digest(status_bytes),
        "tasks": {},
        "dependency_cycle_check": {},
    }
    for task_id in TASK_IDS:
        if task_id in active:
            entry = {"source": "ai-status.json", **{k: active[task_id].get(k) for k in TASK_FIELDS}}
        else:
            archive = status_root / "ai-task-archive" / "tasks" / f"{task_id}.json"
            raw = archive.read_bytes()
            task = json.loads(raw)
            task = task.get("task", task)
            entry = {"source": str(archive.relative_to(status_root)), "source_sha256": digest(raw)}
            entry.update({k: task.get(k) for k in TASK_FIELDS})
            entry["review_submission"] = task.get("review_submission")
        result["tasks"][task_id] = entry
    # Walk every active depends_on edge reachable from the named tasks.
    state, cycles, seen = {}, [], set()

    def visit(node, path):
        if state.get(node) == 1:
            cycles.append(path + [node])
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        seen.add(node)
        for dep in active.get(node, {}).get("depends_on") or []:
            visit(dep, path + [node])
        state[node] = 2

    for task_id in TASK_IDS:
        visit(task_id, [])
    result["dependency_cycle_check"] = {
        "scope": "depends_on edges of active ai-status.json tasks reachable from the named tasks; archived tasks are leaves",
        "nodes_visited": len(seen),
        "cycles": cycles,
    }
    dev_sha = git("rev-parse", "origin/dev").decode().strip()
    readme = git("show", f"origin/dev:{ROLLOUT_README}")
    result["rollout_remediation_evidence"] = {
        "ref": f"origin/dev@{dev_sha}:{ROLLOUT_README}",
        "blob_sha": git("rev-parse", f"origin/dev:{ROLLOUT_README}").decode().strip(),
        "sha256": digest(readme),
        "status_lines": [
            line for line in readme.decode().splitlines()
            if line.startswith("Status:") or line.startswith("- No deployment")
        ],
    }
    return result


def main():
    if sys.argv[1:2] == ["--readback"]:
        print(json.dumps(readback(Path(sys.argv[2])), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    status_root, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(Path(__file__).relative_to(REPO)), "--readback", str(status_root)]
    started = datetime.now(UTC).isoformat()
    proc = subprocess.run(argv, cwd=REPO, capture_output=True)
    finished = datetime.now(UTC).isoformat()
    (out_dir / "readback.stdout.json").write_bytes(proc.stdout)
    (out_dir / "readback.stderr.txt").write_bytes(proc.stderr)
    receipt = {
        "task_id": TASK,
        "purpose": "Read-only re-observation of handover dependency state after reopen; no prior receipt covers 2026-10-03 state.",
        "head_sha": git("rev-parse", "HEAD").decode().strip(),
        "script_blob_sha256": digest(Path(__file__).read_bytes()),
        "script_tracked_at_head": bool(git("ls-tree", "HEAD", "--", str(Path(__file__).relative_to(REPO))).strip()),
        "argv": argv,
        "cwd": str(REPO),
        "started_at": started,
        "finished_at": finished,
        "exit_code": proc.returncode,
        "stdout": {"path": "readback.stdout.json", "bytes": len(proc.stdout), "sha256": digest(proc.stdout)},
        "stderr": {"path": "readback.stderr.txt", "bytes": len(proc.stderr), "sha256": digest(proc.stderr)},
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"exit_code": proc.returncode, "stdout_sha256": receipt["stdout"]["sha256"]}))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
