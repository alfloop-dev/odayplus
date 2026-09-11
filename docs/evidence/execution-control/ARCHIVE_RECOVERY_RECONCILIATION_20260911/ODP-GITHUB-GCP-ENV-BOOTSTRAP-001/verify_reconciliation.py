import json
import sys
from pathlib import Path

def main():
    p = Path(__file__).resolve().parent
    readme_path = p / "README.md"
    json_path = p / "acceptance-reconciliation.json"

    assert readme_path.is_file(), "README.md missing"
    assert json_path.is_file(), "acceptance-reconciliation.json missing"

    # Check trailing whitespace in README
    for i, line in enumerate(readme_path.read_text().splitlines(), 1):
        assert line == line.rstrip(), f"Trailing whitespace in README.md at line {i}"

    data = json.loads(json_path.read_text())
    assert data["task_id"] == "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001", "task_id mismatch"
    assert len(data["acceptance_criteria_reconciliation"]) == 5, "criteria count mismatch"

    # Verify criteria breakdown
    status_counts = {"met": 0, "partially_met": 0, "unmet": 0}
    for item in data["acceptance_criteria_reconciliation"]:
        st = item["status"]
        assert st in status_counts, f"Unexpected status: {st}"
        status_counts[st] += 1

    assert status_counts["met"] == 3, f"Expected 3 met, got {status_counts['met']}"
    assert status_counts["partially_met"] == 2, f"Expected 2 partially_met, got {status_counts['partially_met']}"
    assert status_counts["unmet"] == 0, f"Expected 0 unmet, got {status_counts['unmet']}"

    summary = data["summary"]
    assert summary["criteria_total"] == 5
    assert summary["criteria_met"] == 3
    assert summary["criteria_partially_met"] == 2
    assert summary["criteria_unmet"] == 0
    assert summary["reconciliation_verdict"] == "reconciled_with_known_gaps"
    assert summary["recommendation"] == "blocked"
    assert summary["canonical_dependencies_unchanged"] is True
    assert summary["no_dependency_cycles"] is True

    # Verify DAG check
    dag = data["canonical_dependency_graph_reconciliation"]
    assert dag["depends_on_status"] == "unchanged"
    assert dag["canonical_depends_on_before"] == []
    assert dag["canonical_depends_on_after"] == []
    assert dag["dependency_cycle_check"]["verified_dag"] is True
    assert dag["dependency_cycle_check"]["cycle_detected"] is False

    print("ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 acceptance reconciliation verified successfully.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
