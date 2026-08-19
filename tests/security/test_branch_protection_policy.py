"""Tests for GitHub branch protection policy payload builder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from delivery_toolchain.github.apply_branch_protection import (
    apply_merge_queue,
    branch_policy,
    build_payload,
    build_ruleset_payload,
    delete_merge_queue,
    find_ruleset_id,
    main,
    merge_queue_config,
    report_merge_queue,
    ruleset_name,
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


def test_merge_queue_config_resolution() -> None:
    policy = {
        "branches": {
            "dev": {
                "merge_queue": {
                    "ruleset_name": "dev-merge-queue",
                    "merge_method": "MERGE",
                    "grouping_strategy": "ALLGREEN",
                }
            }
        }
    }
    dev_config = merge_queue_config(policy, "dev")
    assert dev_config is not None
    assert dev_config["ruleset_name"] == "dev-merge-queue"
    assert dev_config["merge_method"] == "MERGE"
    assert dev_config["grouping_strategy"] == "ALLGREEN"

    assert merge_queue_config(policy, "main") is None


def test_merge_queue_config_top_level_fallback() -> None:
    policy = {
        "merge_queue": {
            "dev": {
                "ruleset_name": "dev-merge-queue",
                "merge_method": "MERGE",
            }
        }
    }
    dev_config = merge_queue_config(policy, "dev")
    assert dev_config is not None
    assert dev_config["ruleset_name"] == "dev-merge-queue"
    assert merge_queue_config(policy, "main") is None


def test_build_ruleset_payload() -> None:
    config = {
        "ruleset_name": "custom-queue",
        "merge_method": "MERGE",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 3,
        "max_entries_to_merge": 3,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 2,
        "check_response_timeout_minutes": 45,
    }
    assert ruleset_name(config, "dev") == "custom-queue"
    assert ruleset_name({}, "dev") == "dev-merge-queue"
    payload = build_ruleset_payload(config, "dev")
    assert payload["name"] == "custom-queue"

    assert payload["target"] == "branch"
    assert payload["enforcement"] == "active"
    assert payload["conditions"]["ref_name"]["include"] == ["refs/heads/dev"]
    rules = payload["rules"]
    assert len(rules) == 1
    assert rules[0]["type"] == "merge_queue"
    params = rules[0]["parameters"]
    assert params["merge_method"] == "MERGE"
    assert params["grouping_strategy"] == "ALLGREEN"
    assert params["max_entries_to_build"] == 3
    assert params["max_entries_to_merge"] == 3
    assert params["min_entries_to_merge"] == 1
    assert params["min_entries_to_merge_wait_minutes"] == 2
    assert params["check_response_timeout_minutes"] == 45


def test_shipped_policy_configures_merge_queue_for_dev_only() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    dev_queue = merge_queue_config(policy, "dev")
    main_queue = merge_queue_config(policy, "main")
    assert dev_queue is not None
    assert dev_queue["ruleset_name"] == "dev-merge-queue"
    assert dev_queue["merge_method"] == "MERGE"
    assert dev_queue["grouping_strategy"] == "ALLGREEN"
    assert dev_queue["max_entries_to_build"] == 5
    assert dev_queue["max_entries_to_merge"] == 5
    assert dev_queue["min_entries_to_merge"] == 1
    assert dev_queue["check_response_timeout_minutes"] == 60
    assert main_queue is None


def test_find_ruleset_id() -> None:
    with patch("delivery_toolchain.github.apply_branch_protection.run_gh_cli") as mock_run:
        mock_run.return_value = (0, json.dumps([{"name": "other", "id": 10}, {"name": "dev-merge-queue", "id": 42}]), "")
        assert find_ruleset_id("repo/test", "dev-merge-queue") == 42
        assert find_ruleset_id("repo/test", "nonexistent") is None

        mock_run.return_value = (1, "", "error")
        assert find_ruleset_id("repo/test", "dev-merge-queue") is None


def test_apply_and_delete_merge_queue() -> None:
    config = {"ruleset_name": "dev-merge-queue"}
    with patch("delivery_toolchain.github.apply_branch_protection.find_ruleset_id") as mock_find, \
         patch("delivery_toolchain.github.apply_branch_protection.run_gh_cli") as mock_run:
        # Create path (no existing ruleset)
        mock_find.return_value = None
        mock_run.return_value = (0, "{}", "")
        assert apply_merge_queue("repo/test", "dev", config) is True
        assert mock_run.call_args[0][0][2] == "POST"

        # Update path (existing ruleset)
        mock_find.return_value = 123
        mock_run.return_value = (0, "{}", "")
        assert apply_merge_queue("repo/test", "dev", config) is True
        assert mock_run.call_args[0][0][2] == "PUT"

        # Delete path
        mock_find.return_value = 123
        mock_run.return_value = (0, "", "")
        assert delete_merge_queue("repo/test", "dev", config) is True
        assert mock_run.call_args[0][0][2] == "DELETE"

        # Delete path when absent
        mock_find.return_value = None
        assert delete_merge_queue("repo/test", "dev", config) is True



def test_report_merge_queue() -> None:
    with patch("delivery_toolchain.github.apply_branch_protection.read_merge_queue") as mock_read:
        mock_read.return_value = {"id": "MQ_1", "configuration": {"mergeMethod": "MERGE"}}
        assert report_merge_queue("repo/test", "dev", expect_enabled=True) is True
        assert report_merge_queue("repo/test", "dev", expect_enabled=False) is False

        mock_read.return_value = None
        assert report_merge_queue("repo/test", "dev", expect_enabled=True) is False
        assert report_merge_queue("repo/test", "dev", expect_enabled=False) is True


def test_main_cli_verify_only() -> None:
    with patch("delivery_toolchain.github.apply_branch_protection.run_gh_cli") as mock_run, \
         patch("delivery_toolchain.github.apply_branch_protection.read_merge_queue") as mock_read:
        mock_run.return_value = (0, json.dumps({"required_status_checks": {}}), "")
        mock_read.return_value = {"id": "MQ_1"}
        ret = main(["--verify-only"])
        assert ret == 0
        # When verify-only, PUT is never called
        for call in mock_run.call_args_list:
            assert "-X" not in call[0][0] or call[0][0][call[0][0].index("-X") + 1] != "PUT"


def test_main_cli_disable_merge_queue() -> None:
    with patch("delivery_toolchain.github.apply_branch_protection.run_gh_cli") as mock_run, \
         patch("delivery_toolchain.github.apply_branch_protection.find_ruleset_id") as mock_find, \
         patch("delivery_toolchain.github.apply_branch_protection.read_merge_queue") as mock_read:
        mock_run.return_value = (0, "{}", "")
        mock_find.return_value = 123
        mock_read.return_value = None  # expected to be disabled during rollback
        ret = main(["--disable-merge-queue"])
        assert ret == 0
        # Verify strict was set to True in the payload
        put_calls = [call for call in mock_run.call_args_list if call[1].get("input_data")]
        for call in put_calls:
            payload = json.loads(call[1]["input_data"])
            assert payload["required_status_checks"]["strict"] is True

