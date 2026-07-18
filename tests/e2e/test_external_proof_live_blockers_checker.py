from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_live_blockers.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_live_blockers", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queue_payload() -> dict:
    return {
        "queue": [
            {
                "task_id": "ODP-MAP-STAGE-001",
                "title": "Verify remote staging live tile endpoint",
                "tracking_issue": "https://github.com/alfloop-dev/odayplus/issues/135",
                "fleet_routing": {
                    "required_issue_labels": [
                        "product-e2e",
                        "external-proof",
                        "platform-ops",
                        "release-blocker",
                    ],
                },
            }
        ]
    }


def status_board(status: str = "pending_external_handback") -> dict:
    accepted = status == "accepted"
    return {
        "bundle_status": {"status": "pending_external_handbacks"},
        "tasks": [
            {
                "task_id": "ODP-MAP-STAGE-001",
                "status": status,
                "artifact_check_passed": accepted,
                "accepted_release_head_ref_oid": "abc123" if accepted else None,
                "accepted_at": "2026-06-30T00:00:00Z" if accepted else None,
                "accepted_by": "Product Validation" if accepted else None,
            }
        ],
    }


def issue_payload(
    state: str = "OPEN", *, labels: list[str] | None = None, assignees: list[dict] | None = None
) -> dict:
    if labels is None:
        labels = ["product-e2e", "external-proof", "platform-ops", "release-blocker"]
    if assignees is None:
        assignees = [{"login": "platform-owner"}]
    return {
        "135": {
            "number": 135,
            "state": state,
            "title": "[ODP-MAP-STAGE-001] Verify remote staging live tile endpoint",
            "labels": [{"name": label} for label in labels],
            "assignees": assignees,
        }
    }


def test_validate_live_blockers_accepts_open_pending_issue() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_blockers(
        queue_payload(), status_board(), issue_payload(), require_assignees=True
    )

    assert errors == []


def test_validate_live_blockers_rejects_closed_unaccepted_issue() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_blockers(
        queue_payload(), status_board(), issue_payload(state="CLOSED")
    )

    assert any("must stay open until accepted" in error for error in errors)
    assert any("cannot be closed before Product Validation accepts" in error for error in errors)


def test_validate_live_blockers_allows_closed_accepted_issue() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_blockers(
        queue_payload(), status_board("accepted"), issue_payload(state="CLOSED")
    )

    assert errors == []


def test_validate_live_blockers_rejects_active_issue_missing_labels_or_assignee() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_blockers(
        queue_payload(),
        status_board(),
        issue_payload(labels=["product-e2e"], assignees=[]),
        require_assignees=True,
    )

    assert any("missing active blocker labels" in error for error in errors)
    assert any("has no assignee" in error for error in errors)


def test_validate_live_blockers_rejects_accepted_without_metadata() -> None:
    checker = load_checker_module()
    broken_status = status_board("accepted")
    broken_status["tasks"][0]["artifact_check_passed"] = False
    broken_status["tasks"][0]["accepted_by"] = None

    errors = checker.validate_live_blockers(
        queue_payload(), broken_status, issue_payload(state="CLOSED")
    )

    assert any("artifact_check_passed=true" in error for error in errors)
    assert any("accepted handback missing accepted_by" in error for error in errors)
