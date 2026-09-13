import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

status_root = Path(sys.argv[1]).resolve()
evidence_dir = Path(sys.argv[2]).resolve()
snapshot_bytes = (evidence_dir / "canonical-task-snapshots.json").read_bytes()
historical = json.loads(snapshot_bytes)
board_bytes = (status_root / "ai-status.json").read_bytes()
board = {task["id"]: task for task in json.loads(board_bytes)["tasks"]}
source_cache = {"ai-status.json": board_bytes}
nodes = {}
differences = []
for task_id, old in sorted(historical["nodes"].items()):
    source_file = "ai-status.json" if task_id in board else f"ai-task-archive/tasks/{task_id}.json"
    if source_file == "ai-status.json":
        source = board[task_id]
    else:
        assert source_file == f"ai-task-archive/tasks/{task_id}.json"
        source_cache[source_file] = (status_root / source_file).read_bytes()
        source = json.loads(source_cache[source_file])["task"]
    actual_sha256 = hashlib.sha256(source_cache[source_file]).hexdigest()
    node = {
        "task_id": task_id,
        "source_file": source_file,
        "source_sha256": actual_sha256,
        "status": source.get("status"),
        "owner": source.get("owner"),
        "reviewer": source.get("reviewer"),
        "depends_on": sorted(source.get("depends_on", [])),
    }
    nodes[task_id] = node
    for field in ("status", "owner", "reviewer", "depends_on", "source_sha256"):
        before = sorted(old.get(field, [])) if field == "depends_on" else old.get(field)
        if before != node[field]:
            differences.append({"task_id": task_id, "field": field, "historical": before, "observed_now": node[field]})
edges = [{"from": task_id, "to": dep} for task_id, node in nodes.items() for dep in node["depends_on"]]
assert all(edge["to"] in nodes for edge in edges), "Dependency closure incomplete"
old_edges = [{"from": task_id, "to": dep} for task_id, old in sorted(historical["nodes"].items()) for dep in sorted(old["depends_on"])]
visiting, visited = set(), set()
def visit(task_id):
    assert task_id not in visiting, f"Cycle at {task_id}"
    if task_id in visited:
        return
    visiting.add(task_id)
    for dep in nodes[task_id]["depends_on"]:
        visit(dep)
    visiting.remove(task_id)
    visited.add(task_id)
for task_id in nodes:
    visit(task_id)
result = {
    "observation_time_utc": datetime.now(timezone.utc).isoformat(),
    "repository": "alfloop-dev/odayplus",
    "reviewed_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "scope": "Read-only present-time extraction of only the 17 task IDs declared in the historical snapshot",
    "historical_extraction_status": "Unknown original terminal receipt; this observation does not retroactively establish it",
    "historical_snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
    "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "canonical_status_root": str(status_root),
    "nodes": nodes,
    "edges": edges,
    "edge_set_sha256": hashlib.sha256(json.dumps(edges, sort_keys=True).encode()).hexdigest(),
    "dependencies_match_historical_snapshot": edges == old_edges,
    "cycle_detected": False,
    "differences_from_historical_snapshot": differences,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
