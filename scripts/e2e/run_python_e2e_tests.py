#!/usr/bin/env python3
"""Execute the exact Python acceptance node IDs and write a sealed raw artifact."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e.product_e2e_receipt import (
    PYTEST_NODE_IDS,
    RAW_PYTEST_PATH,
    SCHEMA_VERSION,
    canonical_json_bytes,
    iso_now,
    seal_normalized,
    sha256_bytes,
    source_identity,
)


class ExactResultPlugin:
    """Collect phase-level results without relying on an optional report plugin."""

    def __init__(self) -> None:
        self.reports: dict[str, list[dict[str, Any]]] = {}
        self.collection_errors: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.setdefault(report.nodeid, []).append(
            {
                "phase": report.when,
                "outcome": report.outcome,
                "duration_seconds": report.duration,
                "longrepr": str(report.longrepr) if report.failed else None,
            }
        )

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.append(str(report.longrepr))


def _result_for_node(
    nodeid: str, phase_reports: list[dict[str, Any]]
) -> dict[str, Any]:
    outcomes = {str(report.get("outcome")) for report in phase_reports}
    call_reports = [
        report for report in phase_reports if report.get("phase") == "call"
    ]
    if "failed" in outcomes:
        status = "failed"
    elif "skipped" in outcomes:
        status = "skipped"
    elif len(call_reports) == 1 and call_reports[0].get("outcome") == "passed":
        status = "passed"
    else:
        status = "malformed"
    return {
        "test_id": nodeid,
        "status": status,
        "duration_ms": round(
            sum(float(report.get("duration_seconds") or 0) for report in phase_reports)
            * 1000,
            3,
        ),
        "phases": phase_reports,
    }


def _counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total_specs": 0,
        "total_tests": len(results),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "interrupted": 0,
        "flaky": 0,
        "malformed": 0,
    }
    for result in results:
        status = str(result.get("status"))
        if status in {"passed", "failed", "skipped", "malformed"}:
            counts[status] += 1
        else:
            counts["malformed"] += 1
    return counts


def run_python_tests(
    *,
    output_path: Path = ROOT / RAW_PYTEST_PATH,
    tested_source_sha: str | None = None,
    tested_tree_sha: str | None = None,
) -> int:
    current_source = source_identity(ROOT)
    source_sha = tested_source_sha or os.environ.get("ODP_E2E_TESTED_SOURCE_SHA")
    tree_sha = tested_tree_sha or os.environ.get("ODP_E2E_TESTED_TREE_SHA")
    source_sha = source_sha or current_source["commit_sha"]
    tree_sha = tree_sha or current_source["tree_sha"]

    command_args = [sys.executable, "-m", "pytest", "-q", *PYTEST_NODE_IDS]
    command = shlex.join(command_args)
    plugin = ExactResultPlugin()
    started_at = iso_now()
    pytest_exit = int(pytest.main(["-q", *PYTEST_NODE_IDS], plugins=[plugin]))
    ended_at = iso_now()

    integrity_errors = list(plugin.collection_errors)
    if source_sha != current_source["commit_sha"]:
        integrity_errors.append(
            "runner-start source SHA does not match current committed HEAD"
        )
    if tree_sha != current_source["tree_sha"]:
        integrity_errors.append(
            "runner-start tree SHA does not match current committed tree"
        )
    unexpected_ids = sorted(set(plugin.reports) - set(PYTEST_NODE_IDS))
    missing_ids = sorted(set(PYTEST_NODE_IDS) - set(plugin.reports))
    if unexpected_ids:
        integrity_errors.append(
            "unexpected collected test ids: " + ", ".join(unexpected_ids)
        )
    if missing_ids:
        integrity_errors.append("missing exact test ids: " + ", ".join(missing_ids))

    results = [
        _result_for_node(nodeid, plugin.reports.get(nodeid, []))
        for nodeid in PYTEST_NODE_IDS
    ]
    payload = {
        "requested_node_ids": list(PYTEST_NODE_IDS),
        "phase_reports": plugin.reports,
        "collection_errors": plugin.collection_errors,
    }
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner": "pytest",
        "source": {
            "commit_sha": source_sha,
            "tree_sha": tree_sha,
        },
        "run": {
            "command": command,
            "version": f"pytest {pytest.__version__}; python {sys.version.split()[0]}",
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": pytest_exit,
            "environment": {
                "platform": sys.platform,
                "python_executable": sys.executable,
                "node_id_count": str(len(PYTEST_NODE_IDS)),
            },
        },
        "payload": payload,
        "payload_sha256": sha256_bytes(canonical_json_bytes(payload)),
        "counts": _counts(results),
        "results": results,
        "integrity_errors": integrity_errors,
    }
    seal_normalized(artifact, "normalized_artifact_sha256")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if pytest_exit != 0 or integrity_errors or any(
        result["status"] != "passed" for result in results
    ):
        return pytest_exit or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_python_tests())
