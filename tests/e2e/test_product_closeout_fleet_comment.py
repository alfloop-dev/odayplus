from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
SYNCER = ROOT / "scripts/e2e/sync_product_closeout_fleet_comment.py"
CHECKER = ROOT / "scripts/e2e/check_product_closeout_fleet_notification.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queue_payload() -> dict:
    return json.loads(QUEUE.read_text(encoding="utf-8"))


def rows_for_queue(payload: dict) -> list[dict]:
    readiness_cycle = ("ready", "waiting", "stale_or_invalid")
    rows = []
    for index, entry in enumerate(payload["queue"]):
        rows.append(
            {
                "task_id": entry["task_id"],
                "actor": entry["actor"],
                "action_type": entry["action_type"],
                "queue_status": entry["status"],
                "blocking_type": entry["blocking_type"],
                "readiness": readiness_cycle[index % len(readiness_cycle)],
                "errors": [],
            }
        )
    return rows


def test_product_closeout_fleet_comment_round_trips_through_checker() -> None:
    syncer = load_module(SYNCER, "sync_product_closeout_fleet_comment")
    checker = load_module(CHECKER, "check_product_closeout_fleet_notification")
    queue = queue_payload()
    release_sha = "b7b082d11a9fa2050de566382dd2392ea3ad1927"
    comment = syncer.render_comment(queue, rows_for_queue(queue), release_sha=release_sha)
    pr_payload = {"comments": [{"body": "older comment"}, {"body": comment}]}

    errors = checker.validate_notification(queue, pr_payload, expected_sha=release_sha)

    assert errors == []
    assert "Product closeout fleet update" in comment
    assert "Ready lanes" in comment
    assert "Waiting lanes" in comment
    assert "Blocked or stale lanes" in comment
    assert "Do not mark product release complete" in comment


def test_product_closeout_fleet_notification_rejects_stale_sha() -> None:
    syncer = load_module(SYNCER, "sync_product_closeout_fleet_comment")
    checker = load_module(CHECKER, "check_product_closeout_fleet_notification")
    queue = queue_payload()
    comment = syncer.render_comment(queue, rows_for_queue(queue), release_sha="a" * 40)
    pr_payload = {"comments": [{"body": comment}]}

    errors = checker.validate_notification(queue, pr_payload, expected_sha="b" * 40)

    assert any("missing product closeout fleet update" in error for error in errors)


def test_product_closeout_fleet_notification_rejects_missing_action_command() -> None:
    syncer = load_module(SYNCER, "sync_product_closeout_fleet_comment")
    checker = load_module(CHECKER, "check_product_closeout_fleet_notification")
    queue = queue_payload()
    release_sha = "b7b082d11a9fa2050de566382dd2392ea3ad1927"
    comment = syncer.render_comment(queue, rows_for_queue(queue), release_sha=release_sha)
    comment = comment.replace(
        "check_product_closeout_action.py --task", "missing_action_checker.py --task"
    )
    pr_payload = {"comments": [{"body": comment}]}

    errors = checker.validate_notification(queue, pr_payload, expected_sha=release_sha)

    assert any("missing token: check_product_closeout_action.py" in error for error in errors)
    assert any("missing preflight" in error for error in errors)


def test_product_closeout_fleet_comment_cli_writes_output(tmp_path: Path) -> None:
    status_path = tmp_path / "ai-status.json"
    output_path = tmp_path / "comment.md"
    status_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "ODP-PV-008",
                        "status": "review",
                        "owner": "Codex",
                        "reviewer": "Human/Ops",
                    },
                    {
                        "id": "ODP-FE-XCUT-001",
                        "status": "in_progress",
                        "owner": "Claude2",
                        "reviewer": "Codex",
                    },
                    {
                        "id": "ODP-FE-R0-001",
                        "status": "review_approved",
                        "owner": "Claude",
                        "reviewer": "Codex",
                    },
                    {
                        "id": "ODP-FE-EXP-001",
                        "status": "review",
                        "owner": "Codex",
                        "reviewer": "Claude",
                    },
                    {
                        "id": "ODP-FE-ASSET-001",
                        "status": "in_progress",
                        "owner": "Claude",
                        "reviewer": "Codex2",
                    },
                    {
                        "id": "ODP-FE-XCUT-DOMAIN-001",
                        "status": "review_approved",
                        "owner": "Claude",
                        "reviewer": "Codex",
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SYNCER),
            "--release-sha",
            "b7b082d11a9fa2050de566382dd2392ea3ad1927",
            "--status-path",
            str(status_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    output = output_path.read_text(encoding="utf-8")
    assert "Product closeout fleet update" in output
    assert "check_product_closeout_action_matrix.py" in output
