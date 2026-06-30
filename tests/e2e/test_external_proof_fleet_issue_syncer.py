from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNCER = ROOT / "scripts/e2e/sync_external_proof_fleet_issues.py"
ISSUE_CHECKER = ROOT / "scripts/e2e/check_external_proof_issue_sync.py"
NOTIFICATION_CHECKER = ROOT / "scripts/e2e/check_external_proof_fleet_notifications.py"
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
EXPECTED_SHA = "f14244f4e0f71a949816062839b5cd121fc9696f"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_queue() -> dict:
    return json.loads(QUEUE.read_text(encoding="utf-8"))


def issue_payload_from_rendered(queue: dict, syncer, *, release_sha: str) -> dict:
    payload = {}
    for entry in queue["queue"]:
        issue_number = syncer.issue_number_from_url(entry["tracking_issue"])
        payload[issue_number] = {
            "number": int(issue_number),
            "state": "OPEN",
            "title": syncer.render_issue_title(entry),
            "labels": [{"name": label} for label in entry["fleet_routing"]["required_issue_labels"]],
            "assignees": [{"login": "assigned-owner"}],
            "body": syncer.render_issue_body(entry),
            "comments": [
                {
                    "author": {"login": "release-owner"},
                    "createdAt": "2026-06-30T00:00:00Z",
                    "body": syncer.render_pickup_comment(entry, release_sha),
                }
            ],
        }
    return payload


def test_rendered_issue_bodies_and_comments_pass_existing_checkers() -> None:
    syncer = load_module(SYNCER, "sync_external_proof_fleet_issues")
    issue_checker = load_module(ISSUE_CHECKER, "check_external_proof_issue_sync")
    notification_checker = load_module(
        NOTIFICATION_CHECKER,
        "check_external_proof_fleet_notifications",
    )
    queue = load_queue()
    issues = issue_payload_from_rendered(queue, syncer, release_sha=EXPECTED_SHA)

    assert issue_checker.validate_issue_sync(queue, issues, require_assignees=True) == []
    assert notification_checker.validate_notifications(queue, issues, expected_sha=EXPECTED_SHA) == []


def test_rendered_handoff_includes_single_file_handback_output_flow() -> None:
    syncer = load_module(SYNCER, "sync_external_proof_fleet_issues")
    queue = load_queue()

    for entry in queue["queue"]:
        issue_body = syncer.render_issue_body(entry)
        pickup_comment = syncer.render_pickup_comment(entry, EXPECTED_SHA)

        assert "--output <handback.json>" in issue_body
        assert "--output <handback.json>" in pickup_comment
        assert "check_external_proof_handback_artifact.py <handback.json>" in issue_body
        assert "check_external_proof_handback_artifact.py <handback.json>" in pickup_comment
        assert "check_external_proof_acceptance_readiness.py --report" in issue_body
        assert "check_external_proof_acceptance_readiness.py --report" in pickup_comment
        assert "check_external_proof_acceptance_readiness.py --strict-complete" in issue_body
        assert "check_external_proof_acceptance_readiness.py --strict-complete" in pickup_comment
        assert "expected to fail until every #132-#138 handback" in pickup_comment
        assert "check_external_proof_live_blockers.py --require-assignees" in pickup_comment
        assert entry["completion_rule"] in issue_body
        assert entry["completion_rule"] in pickup_comment


def test_syncer_writes_rendered_output_dirs(tmp_path: Path) -> None:
    issue_dir = tmp_path / "issues"
    comment_dir = tmp_path / "comments"

    result = subprocess.run(
        [
            sys.executable,
            str(SYNCER),
            "--release-sha",
            EXPECTED_SHA,
            "--task",
            "ODP-MAP-STAGE-001",
            "--issue-body-dir",
            str(issue_dir),
            "--comment-body-dir",
            str(comment_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    issue_body = (issue_dir / "ODP-MAP-STAGE-001.md").read_text(encoding="utf-8")
    comment_body = (comment_dir / "ODP-MAP-STAGE-001.md").read_text(encoding="utf-8")
    assert "Task: `ODP-MAP-STAGE-001`" in issue_body
    assert f"PR #82 headRefOid `{EXPECTED_SHA}`" in comment_body
    assert "--output <handback.json>" in comment_body
    assert "check_external_proof_acceptance_readiness.py --report" in comment_body
    assert "check_external_proof_acceptance_readiness.py --strict-complete" in comment_body
    assert "rendered ODP-MAP-STAGE-001 -> issue #135" in result.stdout
