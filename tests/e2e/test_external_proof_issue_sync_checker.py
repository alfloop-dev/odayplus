from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_issue_sync.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_issue_sync", CHECKER)
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
                "tracking_issue": "https://github.com/alfloop-dev/odayplus/issues/135",
                "fleet_routing": {
                    "dispatch_lane": "Platform/Ops live map fleet",
                    "pickup_label": "platform-ops",
                    "required_issue_labels": [
                        "product-e2e",
                        "external-proof",
                        "platform-ops",
                        "release-blocker",
                    ],
                    "pickup_command": "gh issue view 135 --json number,title,labels,body,url",
                    "release_authority": "PR #82 headRefOid and attached checks",
                    "escalation": "Product Validation reviews remote smoke and fallback proof before closure.",
                },
            }
        ]
    }


def synced_issue_payload() -> dict:
    return {
        "135": {
            "number": 135,
            "state": "OPEN",
            "labels": [
                {"name": "product-e2e"},
                {"name": "external-proof"},
                {"name": "platform-ops"},
                {"name": "release-blocker"},
            ],
            "assignees": [{"login": "platform-owner"}],
            "body": "\n".join(
                [
                    "Task: `ODP-MAP-STAGE-001`",
                    "## Fleet pickup routing",
                    "- Dispatch lane: `Platform/Ops live map fleet`",
                    "- Pickup label: `platform-ops`",
                    "- Required issue labels: `product-e2e`, `external-proof`, `platform-ops`, `release-blocker`",
                    "- Pickup command: `gh issue view 135 --json number,title,labels,body,url`",
                    "- Release authority: PR #82 headRefOid and attached checks",
                    "- Escalation: Product Validation reviews remote smoke and fallback proof before closure.",
                    "## Completion rule",
                    "Do not close until proof references PR #82 headRefOid.",
                ]
            ),
        }
    }


def test_validate_issue_sync_accepts_synced_issue() -> None:
    checker = load_checker_module()

    errors = checker.validate_issue_sync(queue_payload(), synced_issue_payload(), require_assignees=True)

    assert errors == []


def test_validate_issue_sync_rejects_missing_labels_and_body_tokens() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["labels"] = [{"name": "product-e2e"}]
    issue["135"]["body"] = "Task: `ODP-MAP-STAGE-001`"

    errors = checker.validate_issue_sync(queue_payload(), issue)

    assert any("missing labels" in error for error in errors)
    assert any("body missing token" in error for error in errors)
    assert any("body missing required label token" in error for error in errors)


def test_validate_issue_sync_can_require_assignees() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["assignees"] = []

    errors = checker.validate_issue_sync(queue_payload(), issue, require_assignees=True)

    assert any("has no assignee" in error for error in errors)


def test_validate_issue_sync_rejects_closed_issue() -> None:
    checker = load_checker_module()
    issue = synced_issue_payload()
    issue["135"]["state"] = "CLOSED"

    errors = checker.validate_issue_sync(queue_payload(), issue)

    assert any("must stay open" in error for error in errors)
