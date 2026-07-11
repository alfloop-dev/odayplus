"""Fixture-driven tests for the product-grade gate reconciliation checker.

The checker reconciles blocker count, pending pickup ACKs, closure packets, and
fleet completion across the evidence surfaces and live ``ai-status.json``. These
tests pin the reconciled numbers against the committed evidence, prove the static
invariants catch queue/board drift, and prove the runtime cross-check classifies
orphaned/stale closure packets and live-implemented blockers. All runtime cases
use synthetic status payloads so the suite stays deterministic in CI where
``ai-status.json`` is not committed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_product_grade_gate_reconciliation.py"
EVIDENCE = ROOT / "docs/evidence"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_product_grade_gate_reconciliation", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sources(module):
    return (
        module.load_json(module.EXTERNAL_QUEUE_PATH),
        module.load_json(module.HANDBACK_BOARD_PATH),
        module.PICKUP_BOARD_PATH.read_text(encoding="utf-8"),
        module.load_json(module.RELEASE_CLOSEOUT_QUEUE_PATH),
    )


def test_committed_evidence_satisfies_static_invariants() -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)

    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, None)
    errors = module.validate_static(reconciliation)

    assert errors == [], errors
    # blocker count and pending pickup ACKs are one and the same while unaccepted.
    assert reconciliation["blocker_count"] == reconciliation["pending_pickup_acks"]
    assert reconciliation["blocker_count"] > 0
    assert set(reconciliation["external_ids"]) == set(reconciliation["handback_board_ids"])


def test_static_invariant_flags_queue_board_mismatch() -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)
    # Drop one board entry so the queue and board no longer agree.
    handback_board = json.loads(json.dumps(handback_board))
    handback_board["tasks"] = handback_board["tasks"][:-1]

    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, None)
    errors = module.validate_static(reconciliation)

    assert any("task ids must match" in error for error in errors), errors


def test_static_invariant_flags_blocker_count_vs_ack_skew() -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)
    # Mark one handback accepted while its external-queue blocker stays open:
    # the board now reports 6 pending while the queue still reports 7 open.
    handback_board = json.loads(json.dumps(handback_board))
    handback_board["tasks"][0]["status"] = "accepted"

    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, None)
    errors = module.validate_static(reconciliation)

    assert reconciliation["queue_open_blocker_count"] != reconciliation["pending_pickup_acks"]
    assert any("open-blocker count must equal" in error for error in errors), errors


def test_runtime_flags_orphaned_and_active_blocker(tmp_path: Path) -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)

    # A live board that omits every closure-packet task and marks one remote
    # staging blocker as an active in-repo task.
    status_payload = {
        "updated_at": "2026-07-11T00:00:00Z",
        "tasks": [
            {"id": "ODP-PV-STAGE-001", "status": "in_progress"},
            {"id": "ODP-R0-001", "status": "done"},
        ],
    }

    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, status_payload)
    findings = module.evaluate_runtime(reconciliation, status_payload)
    kinds = {f["kind"] for f in findings}

    assert "orphaned_closure_packet" in kinds
    assert "blocker_has_active_implementation" in kinds
    assert any(f["task_id"] == "ODP-PV-STAGE-001" for f in findings)


def test_runtime_flags_stale_closure_packet() -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)
    closure_task = release_queue["queue"][1]["task_id"]

    status_payload = {"tasks": [{"id": closure_task, "status": "done"}]}
    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, status_payload)
    findings = module.evaluate_runtime(reconciliation, status_payload)

    assert any(
        f["kind"] == "stale_closure_packet" and f["task_id"] == closure_task for f in findings
    ), findings


def test_runtime_clean_when_status_matches_queue() -> None:
    module = load_checker_module()
    external_queue, handback_board, pickup_text, release_queue = load_sources(module)

    # Build a status payload where every closure-packet task carries a live
    # status equal to one of its queue statuses and no blocker is live.
    queue_statuses: dict[str, str] = {}
    for action in release_queue["queue"]:
        queue_statuses.setdefault(action["task_id"], action["status"])
    status_payload = {"tasks": [{"id": tid, "status": st} for tid, st in queue_statuses.items()]}

    reconciliation = module.reconcile(external_queue, handback_board, pickup_text, release_queue, status_payload)
    findings = module.evaluate_runtime(reconciliation, status_payload)

    assert findings == [], findings


def test_cli_static_mode_passes_on_committed_repo() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--skip-runtime"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "static invariants passed" in result.stdout


def test_cli_strict_runtime_fails_on_drifted_status(tmp_path: Path) -> None:
    status_path = tmp_path / "ai-status.json"
    status_path.write_text(
        json.dumps({"tasks": [{"id": "ODP-UNRELATED", "status": "done"}]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--strict-runtime", "--status-path", str(status_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    # Closure packets are all orphaned against this status, so strict mode fails.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "orphaned_closure_packet" in result.stdout
