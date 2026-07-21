"""Tests for PR merge eligibility policy checks."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_pr_merge_eligibility import check_merge_eligibility


def test_positive_merge_eligible(temp_env: dict[str, Path]) -> None:
    # Scenario: reviewer approval is present and all required CI checks are green (COMPLETED/SUCCESS)
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            # Mock reviews response
            return json.dumps(
                [
                    {
                        "user": {"login": "codex-bot"},
                        "state": "APPROVED",
                    }
                ]
            )
        elif "pr" in args[0] and "view" in args[1]:
            # Mock status check rollup
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {
                            "name": "product-e2e-gate",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is True
    assert len(errors) == 0


def test_negative_review_rejected_ci_green(temp_env: dict[str, Path]) -> None:
    # Scenario: all checks green, but review is rejected (CHANGES_REQUESTED or DISMISSED)
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            return json.dumps(
                [
                    {
                        "user": {"login": "codex-bot"},
                        "state": "CHANGES_REQUESTED",
                    }
                ]
            )
        elif "pr" in args[0] and "view" in args[1]:
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {
                            "name": "product-e2e-gate",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("lacks approval" in err for err in errors)


def test_negative_review_approved_one_failed_check(temp_env: dict[str, Path]) -> None:
    # Scenario: review approved, but product-e2e-gate failed
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            return json.dumps(
                [
                    {
                        "user": {"login": "codex-bot"},
                        "state": "APPROVED",
                    }
                ]
            )
        elif "pr" in args[0] and "view" in args[1]:
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {
                            "name": "product-e2e-gate",
                            "status": "COMPLETED",
                            "conclusion": "FAILURE",
                        },
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("not successful" in err and "product-e2e-gate" in err for err in errors)


def test_negative_review_approved_one_pending_check(temp_env: dict[str, Path]) -> None:
    # Scenario: review approved, but product is pending (IN_PROGRESS)
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            return json.dumps(
                [
                    {
                        "user": {"login": "codex-bot"},
                        "state": "APPROVED",
                    }
                ]
            )
        elif "pr" in args[0] and "view" in args[1]:
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "product", "status": "IN_PROGRESS", "conclusion": None},
                        {
                            "name": "product-e2e-gate",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("not successful" in err and "product" in err for err in errors)


def test_fail_closed_unresolved_reviewer(temp_env: dict[str, Path]) -> None:
    # Scenario: task registry reviewer configuration cannot be resolved (no handles in config)
    # Clear the reviewers in config
    temp_env["config"].write_text(json.dumps({}), encoding="utf-8")

    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("No configured GitHub handles" in err for err in errors)


def test_fail_closed_unresolved_task(temp_env: dict[str, Path]) -> None:
    # Scenario: branch task ID cannot be found in status registry
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-UNKNOWN-TASK",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("not found in status registry" in err for err in errors)


def test_fail_closed_non_review_approved_status(temp_env: dict[str, Path]) -> None:
    # Scenario: task status is in review, not review_approved
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-011",  # Status is "review" in default setup
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("must be 'review_approved'" in err for err in errors)


def test_non_task_branch_fail_closed(temp_env: dict[str, Path]) -> None:
    # Scenario: branch is not task-scoped (e.g. feature/something) and not 'dev'
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="feature/something-new",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is False
    assert any("not task-scoped and is not 'dev'" in err for err in errors)


def test_dev_branch_skip_success(temp_env: dict[str, Path]) -> None:
    # Scenario: branch is 'dev' (promotion PR head), should bypass task checks
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="dev",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is True
    assert len(errors) == 0


def test_status_context_success(temp_env: dict[str, Path]) -> None:
    # Scenario: status checks are represented as StatusContext (using 'context' and 'state')
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            return json.dumps(
                [
                    {
                        "user": {"login": "codex-bot"},
                        "state": "APPROVED",
                    }
                ]
            )
        elif "pr" in args[0] and "view" in args[1]:
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"context": "orchestrator", "state": "SUCCESS"},
                        {"context": "product", "state": "SUCCESS"},
                        {"context": "product-e2e-gate", "state": "SUCCESS"},
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is True
    assert len(errors) == 0


def test_fallback_to_canonical_status_on_missing_github_review(temp_env: dict[str, Path]) -> None:
    # Scenario: GitHub reviews are empty/absent, but canonical status is review_approved
    def mock_gh_runner(args: list[str], repo: str | None = None) -> str:
        if any("reviews" in str(arg) for arg in args):
            return "[]"  # No reviews
        elif "pr" in args[0] and "view" in args[1]:
            return json.dumps(
                {
                    "statusCheckRollup": [
                        {"name": "orchestrator", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {
                            "name": "product-e2e-gate",
                            "status": "COMPLETED",
                            "conclusion": "SUCCESS",
                        },
                    ]
                }
            )
        return "[]"

    eligible, errors = check_merge_eligibility(
        pr_number=82,
        branch_name="task/ODP-OC-R5-012",
        repo_slug="alfloop-dev/odayplus",
        status_path=temp_env["status"],
        config_path=temp_env["config"],
        policy_path=temp_env["policy"],
        gh_runner=mock_gh_runner,
    )

    assert eligible is True
    assert len(errors) == 0
