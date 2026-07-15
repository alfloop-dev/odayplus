"""Tests for GitHub branch protection policy payload builder."""

from __future__ import annotations

from scripts.apply_branch_protection import build_payload


def test_build_payload_with_reviews() -> None:
    policy = {
        "required_status_checks": ["orchestrator", "product"],
        "enforce_admins": True,
        "required_approving_review_count": 2,
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": False,
    }
    payload = build_payload(policy)
    assert payload["required_status_checks"]["strict"] is True
    assert payload["required_status_checks"]["contexts"] == ["orchestrator", "product"]
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"] == {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 2,
    }
    assert payload["restrictions"] is None


def test_build_payload_without_reviews() -> None:
    policy = {
        "required_status_checks": [
            "orchestrator",
            "product",
            "product-e2e-gate",
            "task-review-gate",
        ],
        "enforce_admins": True,
    }
    payload = build_payload(policy)
    assert payload["required_status_checks"]["strict"] is True
    assert payload["required_status_checks"]["contexts"] == [
        "orchestrator",
        "product",
        "product-e2e-gate",
        "task-review-gate",
    ]
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"] is None
    assert payload["restrictions"] is None
