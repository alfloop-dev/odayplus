"""Tests for GitHub branch protection policy payload builder."""

from __future__ import annotations

import json
from pathlib import Path

from delivery_toolchain.github.apply_branch_protection import (
    branch_policy,
    build_payload,
)

POLICY_PATH = Path(__file__).resolve().parents[2] / ".github/branch-protection/policy.json"


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


def test_branch_policy_overlays_only_declared_deltas() -> None:
    policy = {
        "required_status_checks": ["orchestrator"],
        "enforce_admins": True,
        "branches": {"dev": {"strict": False}},
    }
    resolved = branch_policy(policy, "dev")
    assert resolved["strict"] is False
    # Shared settings are declared once at the top level and survive the overlay.
    assert resolved["required_status_checks"] == ["orchestrator"]
    assert resolved["enforce_admins"] is True


def test_branch_policy_leaves_unlisted_branches_untouched() -> None:
    policy = {
        "required_status_checks": ["orchestrator"],
        "enforce_admins": True,
        "branches": {"dev": {"strict": False}},
    }
    assert branch_policy(policy, "main") == policy
    assert build_payload(branch_policy(policy, "main"))["required_status_checks"]["strict"] is True


def test_branch_policy_without_branches_key_preserves_strict() -> None:
    policy = {"required_status_checks": ["orchestrator"], "enforce_admins": True}
    assert branch_policy(policy, "dev") == policy
    assert build_payload(branch_policy(policy, "dev"))["required_status_checks"]["strict"] is True


def test_shipped_policy_disarms_strict_on_dev_only() -> None:
    # dev sits behind the merge queue, which already tests each candidate on a
    # ref built from the base plus the queued PRs; strict would only re-add the
    # rebase race and wall every PR off as BEHIND.
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    dev = build_payload(branch_policy(policy, "dev"))
    main = build_payload(branch_policy(policy, "main"))
    assert dev["required_status_checks"]["strict"] is False
    assert main["required_status_checks"]["strict"] is True
    # Everything else stays identical across the two branches.
    assert dev["required_status_checks"]["contexts"] == main["required_status_checks"]["contexts"]
    assert dev["enforce_admins"] is main["enforce_admins"] is True
