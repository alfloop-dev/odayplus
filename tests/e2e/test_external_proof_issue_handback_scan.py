from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_issue_handback_scan.py"
EXPECTED_SHA = "11f4fb625f922c5e2d178508128e9106da7c465a"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_issue_handback_scan", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue_payload(*, pickup_sha: str = EXPECTED_SHA, handback_body: str | None = None) -> dict:
    comments = [
        {
            "author": {"login": "release-owner"},
            "createdAt": "2026-06-30T12:00:00Z",
            "body": (
                "## External proof fleet pickup update\n\n"
                f"Current release target: PR #82 headRefOid `{pickup_sha}`.\n\n"
                "Task: `ODP-MAP-STAGE-001`"
            ),
        }
    ]
    if handback_body is not None:
        comments.append(
            {
                "author": {"login": "platform-ops"},
                "createdAt": "2026-06-30T12:10:00Z",
                "body": handback_body,
            }
        )
    return {"number": 135, "state": "OPEN", "comments": comments}


def test_issue_handback_scan_reports_no_handback_after_latest_pickup() -> None:
    checker = load_checker_module()

    row = checker.scan_issue_for_handbacks(
        task_id="ODP-MAP-STAGE-001",
        issue=issue_payload(),
        expected_sha=EXPECTED_SHA,
    )

    assert row["status"] == "no_handback_after_latest_pickup"
    assert row["latest_pickup_created_at"] == "2026-06-30T12:00:00Z"
    assert row["candidate_handback_comments"] == []


def test_issue_handback_scan_detects_candidate_handback_after_latest_pickup() -> None:
    checker = load_checker_module()

    row = checker.scan_issue_for_handbacks(
        task_id="ODP-MAP-STAGE-001",
        issue=issue_payload(
            handback_body=(
                "External proof handback for ODP-MAP-STAGE-001\n"
                f"release_head_ref_oid: {EXPECTED_SHA}\n"
                "python3 scripts/e2e/check_external_proof_handback_artifact.py handback.json"
            )
        ),
        expected_sha=EXPECTED_SHA,
    )

    assert row["status"] == "candidate_handback_detected"
    assert row["candidate_handback_comments"] == [
        {
            "author": "platform-ops",
            "created_at": "2026-06-30T12:10:00Z",
            "contains_expected_sha": True,
            "contains_artifact_checker": True,
        }
    ]


def test_issue_handback_scan_rejects_missing_current_sha_pickup() -> None:
    checker = load_checker_module()

    row = checker.scan_issue_for_handbacks(
        task_id="ODP-MAP-STAGE-001",
        issue=issue_payload(pickup_sha="cd1a58902432ef891e27ba22244d159b1e7ba850"),
        expected_sha=EXPECTED_SHA,
    )

    errors = checker.validate_scan([row])

    assert row["status"] == "missing_current_sha_pickup"
    assert errors == ["ODP-MAP-STAGE-001 missing_current_sha_pickup"]


def test_issue_handback_scan_renders_report() -> None:
    checker = load_checker_module()
    row = checker.scan_issue_for_handbacks(
        task_id="ODP-MAP-STAGE-001",
        issue=issue_payload(),
        expected_sha=EXPECTED_SHA,
    )

    report = checker.render_markdown([row], expected_sha=EXPECTED_SHA)

    assert "External Proof Issue Handback Scan" in report
    assert "ODP-MAP-STAGE-001" in report
    assert "no_handback_after_latest_pickup" in report
