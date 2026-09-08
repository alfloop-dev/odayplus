"""Record which production monitoring entry points a test selection actually executes.

The four-dimension monitoring acceptance for ODP-DRIFT-SECURITY-VERIFY-003 asks
for *production-entry* evidence, not for a rerun of low-level unit tests. A test
module name proves nothing about which code path ran, so this probe wraps every
production entry point with a pass-through recorder, runs the selected node ids
in-process, and writes down what was reached.

The recorders do not change behaviour: arguments and the return value are passed
through untouched and an exception is re-raised after being noted. An entry that
is never called ends up with ``calls: 0`` in the receipt, so the receipt cannot
report coverage that the run did not produce.

Nothing is installed: the probe imports only the standard library plus the
already-installed ``pytest``, leaving the audited candidate scope untouched.

Usage::

    uv run --frozen --python 3.12 python \
        docs/evidence/completion/ODP-DRIFT-SECURITY-VERIFY-003/tools/production_entry_probe.py \
        --out <receipt.json> --log <pytest-output.txt>
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
EVIDENCE_ID = "ODP-DRIFT-SECURITY-VERIFY-003"

# Dimension -> node ids. Every selected node id must drive a production entry
# below; a dimension whose entries stay at zero calls is reported as uncovered.
SELECTION: dict[str, tuple[str, ...]] = {
    "data_drift": (
        "tests/models/test_evidently_monitor.py::test_evidently_monitor_persists_real_report_payload",
        "tests/integration/test_oss_ai_execution_flow.py::test_governed_training_registry_and_monitoring_flow_uses_real_oss",
    ),
    "feature_drift": (
        "tests/models/test_evidently_monitor.py::test_evidently_monitor_detects_shifted_features",
        "tests/models/test_evidently_monitor_baseline.py::test_first_party_column_list_agrees_with_the_engine_drift_count",
    ),
    "prediction_drift": (
        "modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_service_persists_receipt_and_alert",
        "modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_service_rejects_non_production_model_version",
        "modules/learninghub/tests/test_prediction_drift.py::test_prediction_drift_rejects_mixed_cohort_and_version_rows",
    ),
    "performance_drift": (
        "modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py::test_evaluate_monitoring_triggers_retraining_on_performance_drift",
        "modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py::test_decision_policy_governs_performance_drift_thresholds",
        "tests/integration/test_learninghub_release.py::test_release_monitor_api_forwards_explicit_baseline_metrics",
        "tests/integration/test_learninghub_release.py::test_release_monitor_breach_recommends_rollback_and_leaves_alias_unchanged",
        "tests/integration/test_learninghub_release.py::test_release_monitor_rejects_unknown_release",
        "tests/integration/test_production_model_lifecycle.py::test_monitoring_comparison_restart_safety_and_governed_rollback",
    ),
}

# entry key -> (module, class, attribute, dimensions it serves)
ENTRIES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "EvidentlyDriftMonitor.run": (
        "modules.learninghub.infrastructure.evidently_monitor",
        "EvidentlyDriftMonitor",
        "run",
        ("data_drift", "feature_drift"),
    ),
    "EvidentlyDriftMonitor.run_prediction": (
        "modules.learninghub.infrastructure.evidently_monitor",
        "EvidentlyDriftMonitor",
        "run_prediction",
        ("prediction_drift",),
    ),
    "LearningHubService.monitor_prediction_drift": (
        "modules.learninghub.application.release",
        "LearningHubService",
        "monitor_prediction_drift",
        ("prediction_drift",),
    ),
    "LearningHubService.evaluate_monitoring": (
        "modules.learninghub.application.release",
        "LearningHubService",
        "evaluate_monitoring",
        ("performance_drift",),
    ),
    "LearningHubService.ingest_outcome_monitoring": (
        "modules.learninghub.application.release",
        "LearningHubService",
        "ingest_outcome_monitoring",
        ("performance_drift",),
    ),
    "LearningHubService.monitor_release": (
        "modules.learninghub.application.release",
        "LearningHubService",
        "monitor_release",
        ("performance_drift",),
    ),
}

# Governed inputs the acceptance asks to stay observable at the entry: the cohort
# the comparison is bound to, the thresholds it is judged against, and the error
# surface. Recorded as presence + shape, never as row-level payload.
GOVERNED_KWARGS = (
    "cohort_key",
    "signal_type",
    "model_version",
    "drift_share_threshold",
    "prediction_columns",
    "output_types",
    "policy",
    "decision_policy",
    "thresholds",
    "guardrails",
)

# Input files whose content decides the recorded outcome. Hashing them pins the
# receipt to the tree that produced it, independently of later evidence commits.
INPUT_FILES = (
    "modules/learninghub/infrastructure/evidently_monitor.py",
    "modules/learninghub/infrastructure/native_drift.py",
    "modules/learninghub/application/release.py",
    "modules/learninghub/application/monitor.py",
    "modules/learninghub/domain/monitoring.py",
    "models/shared_ml/validation.py",
    "apps/api/app/routes/learninghub.py",
    "tests/models/test_evidently_monitor.py",
    "tests/models/test_evidently_monitor_baseline.py",
    "modules/learninghub/tests/test_prediction_drift.py",
    "modules/learninghub/tests/test_performance_drift_and_baseline_comparison.py",
    "tests/integration/test_learninghub_release.py",
    "tests/integration/test_production_model_lifecycle.py",
    "tests/integration/test_oss_ai_execution_flow.py",
)

RECORDS: dict[str, list[dict[str, Any]]] = {name: [] for name in ENTRIES}


def _describe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return {"kind": type(value).__name__, "len": len(value)}
    if isinstance(value, dict):
        return {"kind": "dict", "keys": sorted(str(key) for key in value)}
    name = getattr(value, "value", None)
    if isinstance(name, str):
        return f"{type(value).__name__}.{name}"
    identifier = getattr(value, "policy_version_id", None)
    if isinstance(identifier, str):
        return f"{type(value).__name__}({identifier})"
    return type(value).__name__


def _install(entry: str) -> None:
    module_name, class_name, attribute, _ = ENTRIES[entry]
    module = __import__(module_name, fromlist=[class_name])
    owner = getattr(module, class_name)
    original = getattr(owner, attribute)

    @functools.wraps(original)
    def recorder(*args: Any, **kwargs: Any) -> Any:
        observed = {
            key: _describe(kwargs[key]) for key in GOVERNED_KWARGS if key in kwargs
        }
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised unchanged below
            RECORDS[entry].append(
                {
                    "outcome": f"raised:{type(exc).__name__}",
                    "message": str(exc)[:200],
                    "governed_kwargs": observed,
                }
            )
            raise
        RECORDS[entry].append(
            {"outcome": "returned", "governed_kwargs": observed}
        )
        return result

    setattr(owner, attribute, recorder)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return _git_raw(*args).strip()


def _git_raw(*args: str) -> str:
    """Unstripped output; ``git status --porcelain`` encodes state in column 1."""

    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    options = parser.parse_args()
    out_path = options.out.resolve()
    log_path = options.log.resolve()

    sys.path.insert(0, str(REPO_ROOT))
    for entry in ENTRIES:
        _install(entry)

    import pytest

    node_ids = [node for nodes in SELECTION.values() for node in nodes]
    started_at = datetime.now(UTC)
    with log_path.open("w", encoding="utf-8") as stream:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            exit_code = int(pytest.main(["-p", "no:cacheprovider", "--tb=short", *node_ids]))
        finally:
            sys.stdout, sys.stderr = stdout, stderr
    finished_at = datetime.now(UTC)

    installed = {
        dist.metadata["Name"].lower()
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"]
    }

    entries: dict[str, Any] = {}
    for entry, (module_name, class_name, attribute, dimensions) in ENTRIES.items():
        calls = RECORDS[entry]
        outcomes: dict[str, int] = {}
        for call in calls:
            outcomes[call["outcome"]] = outcomes.get(call["outcome"], 0) + 1
        entries[entry] = {
            "production_symbol": f"{module_name}.{class_name}.{attribute}",
            "serves_dimensions": list(dimensions),
            "calls": len(calls),
            "outcomes": outcomes,
            "observed_governed_kwargs": sorted(
                {key for call in calls for key in call["governed_kwargs"]}
            ),
            "call_log": calls,
        }

    dimensions_report: dict[str, Any] = {}
    for dimension, nodes in SELECTION.items():
        serving = [
            entry for entry, spec in ENTRIES.items() if dimension in spec[3]
        ]
        executed = [entry for entry in serving if entries[entry]["calls"] > 0]
        raised = sorted(
            {
                outcome
                for entry in executed
                for outcome in entries[entry]["outcomes"]
                if outcome.startswith("raised:")
            }
        )
        dimensions_report[dimension] = {
            "selected_node_ids": list(nodes),
            "production_entries": serving,
            "production_entries_executed": executed,
            "covered": bool(executed),
            "refusal_outcomes_observed": raised,
        }

    receipt = {
        "evidence_id": EVIDENCE_ID,
        "receipt_kind": "production_entry_execution",
        "generated_at": finished_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "run_sha": _git("rev-parse", "HEAD"),
        "run_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_clean_at_run": _git("status", "--porcelain") == "",
        # Named so a reviewer can confirm the run was not taken over uncommitted
        # production or test edits; only evidence paths may appear here.
        "worktree_dirty_paths": sorted(
            line.split(maxsplit=1)[1]
            for line in _git_raw("status", "--porcelain").splitlines()
            if line.strip()
        ),
        "python_version": sys.version,
        "platform": platform.platform(),
        "pytest_version": pytest.__version__,
        "pytest_argv": ["-p", "no:cacheprovider", "--tb=short", *node_ids],
        "pytest_exit_code": exit_code,
        "pytest_log": str(log_path.relative_to(REPO_ROOT)),
        "installed_scope_excludes": {
            name: name not in installed for name in ("evidently", "nltk")
        },
        "input_file_sha256": {
            path: _sha256(REPO_ROOT / path) for path in INPUT_FILES
        },
        "entries": entries,
        "dimensions": dimensions_report,
        "all_dimensions_covered": all(
            report["covered"] for report in dimensions_report.values()
        ),
    }
    out_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
