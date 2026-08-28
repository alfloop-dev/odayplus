"""Tests for delivery toolchain gates, focusing on enforce_delivery_merged_gate and cross-repo delivery."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
DELIVERY_GIT_DIR = ROOT / "delivery_toolchain" / "git"

for p in (SCRIPTS_DIR, ORCHESTRATOR_DIR, DELIVERY_GIT_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ai_status

TASK_ID = "ODP-TEST-DELIVERY-001"
APPROVED_HEAD = "1111111111111111111111111111111111111111"
MERGE_COMMIT = "2222222222222222222222222222222222222222"
POST_MERGE_HEAD = "3333333333333333333333333333333333333333"
DEFAULT_REPO_SLUG = "alfloop-dev/odayplus"
CROSS_REPO_SLUG = "external-org/external-repo"


def _make_merged_pr_status(
    *,
    task_id: str = TASK_ID,
    approved_head: str = APPROVED_HEAD,
    merge_commit: str = MERGE_COMMIT,
    base_branch: str = "dev",
) -> dict[str, object]:
    return {
        "number": 999,
        "url": f"https://github.com/example/{task_id}/pull/999",
        "state": "MERGED",
        "headRefName": f"task/{task_id}",
        "headRefOid": approved_head,
        "baseRefName": base_branch,
        "mergedAt": "2026-08-28T00:00:00Z",
        "mergeCommit": {"oid": merge_commit},
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "orchestrator",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "StatusContext",
                "context": "task-review-gate",
                "state": "SUCCESS",
            },
        ],
    }


def _run_enforce_delivery_merged_gate(
    *,
    delivery: dict[str, object],
    pr_status: dict[str, object] | None,
    repository_slug_value: str | None = DEFAULT_REPO_SLUG,
    repository_id: str = "pantheon",
    task_id: str = TASK_ID,
    approved_head: str = APPROVED_HEAD,
    merge_commit: str = MERGE_COMMIT,
    target_branch: str = "dev",
    merge_on_target: bool = True,
    remote_names: list[str] | None = None,
    config: dict[str, object] | None = None,
) -> None:
    if remote_names is None:
        remote_names = ["origin"]
    if config is None:
        config = {"branch_workflow": {"dev_branch": target_branch}}

    def fake_git(args: list[str], **kwargs: object) -> str:
        if args == ["fetch", "origin", target_branch]:
            return ""
        if args == ["rev-parse", "--verify", f"origin/{target_branch}"]:
            return "dev-tip"
        raise AssertionError(f"unexpected git command: {args}")

    def fake_succeeds(args: list[str], **kwargs: object) -> bool:
        if args == ["merge-base", "--is-ancestor", approved_head, f"origin/{target_branch}"]:
            return False
        if args == ["merge-base", "--is-ancestor", merge_commit, f"origin/{target_branch}"]:
            return merge_on_target
        raise AssertionError(f"unexpected git check: {args}")

    with (
        mock.patch.object(ai_status, "run_git_command", side_effect=fake_git),
        mock.patch.object(ai_status, "git_command_succeeds", side_effect=fake_succeeds),
        mock.patch.object(ai_status, "pull_request_status_for_branch", return_value=pr_status),
    ):
        ai_status.enforce_delivery_merged_gate(
            config,
            delivery,
            repository_root=Path("/task-checkout"),
            repository_id=repository_id,
            branch=f"task/{task_id}",
            remote_names=remote_names,
            approved_head=approved_head,
            repository_slug_value=repository_slug_value,
        )


def test_delivery_merged_gate_same_repo_matching_head() -> None:
    """Verify same-repo delivery gate succeeds when verified_head equals approved_head."""
    delivery: dict[str, object] = {"verified_head": APPROVED_HEAD}
    pr_status = _make_merged_pr_status()

    with mock.patch.object(ai_status, "is_approved_head_satisfied") as mock_satisfied:
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=pr_status,
            repository_slug_value=DEFAULT_REPO_SLUG,
        )
        mock_satisfied.assert_not_called()

    assert delivery["merge_verified_via_pr"] is True
    assert delivery["ci_status"] == "success"
    assert delivery["pull_request"]["head_sha"] == APPROVED_HEAD
    assert delivery["pull_request"]["repository"] == DEFAULT_REPO_SLUG
    assert "post_merge_checkout_advanced" not in delivery


def test_delivery_merged_gate_same_repo_post_merge_checkout_advance() -> None:
    """Verify same-repo delivery gate passes repository slug to is_approved_head_satisfied when checkout head advanced."""
    delivery: dict[str, object] = {"verified_head": POST_MERGE_HEAD}
    pr_status = _make_merged_pr_status()

    with mock.patch.object(ai_status, "is_approved_head_satisfied", return_value=True) as mock_satisfied:
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=pr_status,
            repository_slug_value=DEFAULT_REPO_SLUG,
        )
        mock_satisfied.assert_called_once_with(
            {
                "id": TASK_ID,
                "approved_head": APPROVED_HEAD,
                "repository": DEFAULT_REPO_SLUG,
            },
            POST_MERGE_HEAD,
            APPROVED_HEAD,
            repository_root=Path("/task-checkout"),
        )

    assert delivery["merge_verified_via_pr"] is True
    assert delivery["post_merge_checkout_advanced"] is True


def test_delivery_merged_gate_cross_repo_repository_slug_propagation() -> None:
    """Verify cross-repo delivery gate passes the cross-repo repository slug to is_approved_head_satisfied."""
    delivery: dict[str, object] = {"verified_head": POST_MERGE_HEAD}
    pr_status = _make_merged_pr_status(base_branch="main")

    with mock.patch.object(ai_status, "is_approved_head_satisfied", return_value=True) as mock_satisfied:
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=pr_status,
            repository_slug_value=CROSS_REPO_SLUG,
            repository_id="external_repo",
            target_branch="main",
        )
        mock_satisfied.assert_called_once_with(
            {
                "id": TASK_ID,
                "approved_head": APPROVED_HEAD,
                "repository": CROSS_REPO_SLUG,
            },
            POST_MERGE_HEAD,
            APPROVED_HEAD,
            repository_root=Path("/task-checkout"),
        )

    assert delivery["merge_verified_via_pr"] is True
    assert delivery["pull_request"]["repository"] == CROSS_REPO_SLUG
    assert delivery["post_merge_checkout_advanced"] is True


def test_delivery_merged_gate_rejects_unapproved_checkout_drift() -> None:
    """Verify delivery gate rejects checkout head drift if is_approved_head_satisfied returns False."""
    delivery: dict[str, object] = {"verified_head": POST_MERGE_HEAD}
    pr_status = _make_merged_pr_status()

    with mock.patch.object(ai_status, "is_approved_head_satisfied", return_value=False):
        with pytest.raises(SystemExit, match="task-owned checkout HEAD .* differs from reviewer-approved head"):
            _run_enforce_delivery_merged_gate(
                delivery=delivery,
                pr_status=pr_status,
                repository_slug_value=CROSS_REPO_SLUG,
            )


def test_delivery_merged_gate_rejects_missing_remote() -> None:
    """Verify delivery gate rejects finalization when remote is missing."""
    delivery: dict[str, object] = {"verified_head": APPROVED_HEAD}
    with pytest.raises(SystemExit, match="the repository has no git remote"):
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=None,
            remote_names=[],
        )


def test_delivery_merged_gate_rejects_missing_repository_slug() -> None:
    """Verify delivery gate rejects finalization when repository slug is missing."""
    delivery: dict[str, object] = {"verified_head": APPROVED_HEAD}
    with pytest.raises(SystemExit, match="configured repository slug is unavailable"):
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=None,
            repository_slug_value=None,
        )


def test_delivery_merged_gate_rejects_unmerged_pr() -> None:
    """Verify delivery gate rejects finalization when PR is OPEN."""
    delivery: dict[str, object] = {"verified_head": APPROVED_HEAD}
    pr_status = _make_merged_pr_status()
    pr_status["state"] = "OPEN"

    with pytest.raises(SystemExit, match="immutable approved-head PR provenance does not prove delivery"):
        _run_enforce_delivery_merged_gate(
            delivery=delivery,
            pr_status=pr_status,
        )
