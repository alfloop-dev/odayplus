#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import common
import github_bus
import github_cloud_relay
from common import load_jsonl
from github_command_parser import GitHubCommand


class GitHubBusCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        github_bus.clear_remote_branch_snapshot_cache()
        self.config = {
            "github_bus": {
                "reviewers": {
                    "Claude": ["ajoe734"],
                    "Codex": ["ajoe734"],
                }
            }
        }
        self.bus_state = {"tasks": {}}

    def test_apply_bus_command_review_approve_uses_reviewer_actor(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "LIN-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "next": "ready for review",
                }
            ]
        }
        command = GitHubCommand(verb="approve", target="LIN-001", raw="/approve LIN-001")

        with (
            mock.patch.object(github_bus, "run_ai_status") as run_ai_status,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed, reply = github_bus.apply_bus_command(
                self.config,
                self.bus_state,
                status,
                "ajoe734/pantheon",
                command,
                "ajoe734",
                issue_number=4,
            )

        self.assertTrue(changed)
        self.assertEqual(reply, "Applied `/approve` to `LIN-001`.")
        run_ai_status.assert_called_once_with(
            "approve",
            "LIN-001",
            "GitHub approval bus approved via issue #4 by @ajoe734.",
            actor="Claude",
        )

    def test_poll_pr_reviews_approved_uses_reviewer_approval(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "LIN-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "next": "ready for review",
                }
            ]
        }
        bus_state = {
            "processed_review_ids": [],
            "tasks": {
                "LIN-001": {
                    "review_pr": {"number": 12},
                }
            },
        }

        with (
            mock.patch.object(
                github_bus,
                "gh_json",
                side_effect=[
                    [
                        {
                            "id": 999,
                            "state": "APPROVED",
                            "body": "looks good",
                            "user": {"login": "ajoe734"},
                        }
                    ],
                    {
                        "statusCheckRollup": [],
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "state": "OPEN",
                        "mergedAt": None,
                    },
                ],
            ),
            mock.patch.object(github_bus, "run_ai_status") as run_ai_status,
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertTrue(changed)
        run_ai_status.assert_called_once_with(
            "approve",
            "LIN-001",
            "GitHub PR approved via PR #12 by @ajoe734.",
            actor="Claude",
        )
        self.assertEqual(bus_state["processed_review_ids"], ["review:999"])
        write_activity_log.assert_called_once()

    def test_poll_pr_reviews_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"pr_reviews": 2}
        status = {
            "tasks": [
                {"id": "LIN-001", "reviewer": "Claude"},
                {"id": "LIN-002", "reviewer": "Claude"},
                {"id": "LIN-003", "reviewer": "Claude"},
            ]
        }
        bus_state = {
            "processed_review_ids": [],
            "poll_cursors": {"pr_reviews": 0},
            "tasks": {
                "LIN-001": {"review_pr": {"number": 11}},
                "LIN-002": {"review_pr": {"number": 12}},
                "LIN-003": {"review_pr": {"number": 13}},
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        review_calls = [call.args[0][-1] for call in gh_json.call_args_list if call.args[0][0] == "api"]
        self.assertEqual(
            review_calls,
            [
                "repos/ajoe734/pantheon/pulls/11/reviews?per_page=100",
                "repos/ajoe734/pantheon/pulls/12/reviews?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["pr_reviews"], 2)

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_pr_reviews(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        review_calls = [call.args[0][-1] for call in gh_json.call_args_list if call.args[0][0] == "api"]
        self.assertEqual(
            review_calls,
            ["repos/ajoe734/pantheon/pulls/13/reviews?per_page=100"],
        )
        self.assertEqual(bus_state["poll_cursors"]["pr_reviews"], 0)

    def test_poll_issue_comments_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"issue_comments": 2}
        status = {
            "tasks": [
                {"id": "LIN-001", "reviewer": "Claude"},
                {"id": "LIN-002", "reviewer": "Claude"},
                {"id": "LIN-003", "reviewer": "Claude"},
            ]
        }
        bus_state = {
            "processed_comment_ids": [],
            "poll_cursors": {"issue_comments": 0},
            "tasks": {
                "LIN-001": {"ops_issue": {"number": 21}},
                "LIN-002": {"ops_issue": {"number": 22}},
                "LIN-003": {"ops_issue": {"number": 23}},
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_issue_comments(self.config, bus_state, status, "ajoe734/pantheon")

        self.assertFalse(changed)
        self.assertEqual(
            [call.args[0][-1] for call in gh_json.call_args_list],
            [
                "repos/ajoe734/pantheon/issues/21/comments?per_page=100",
                "repos/ajoe734/pantheon/issues/22/comments?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["issue_comments"], 2)

    def test_poll_coordination_issue_comments_batches_with_cursor(self) -> None:
        self.config["github_bus"]["poll_batch_sizes"] = {"coordination_comments": 2}
        bus_state = {
            "processed_comment_ids": [],
            "poll_cursors": {"coordination_comments": 0},
            "coordination": {
                "ajoe734/pantheon:F-001": {"repo": "ajoe734/pantheon", "issue": {"number": 31}},
                "ajoe734/pantheon:F-002": {"repo": "ajoe734/pantheon", "issue": {"number": 32}},
                "ajoe734/front-ai-trading-system:F-003": {
                    "repo": "ajoe734/front-ai-trading-system",
                    "issue": {"number": 33},
                },
            },
        }

        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            changed = github_bus.poll_coordination_issue_comments(
                self.config,
                bus_state,
                {"tasks": []},
                runtime_state={},
            )

        self.assertFalse(changed)
        self.assertEqual(
            [call.args[0][-1] for call in gh_json.call_args_list],
            [
                "repos/ajoe734/pantheon/issues/31/comments?per_page=100",
                "repos/ajoe734/pantheon/issues/32/comments?per_page=100",
            ],
        )
        self.assertEqual(bus_state["poll_cursors"]["coordination_comments"], 2)

    def test_upsert_review_pr_records_missing_pr_without_creating(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "master",
                "reviewers": {"Claude": ["ajoe734"]},
                "labels": {"review": ["pantheon-bus", "pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }
        bus_state = {"tasks": {}}
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": ["foo.md"],
            "next": "ready for review",
        }

        with (
            mock.patch.object(github_bus, "branch_exists", side_effect=lambda branch: branch == "feature/lin-001"),
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(
                github_bus,
                "remote_branch_exists",
                side_effect=lambda branch: branch == "feature/lin-001",
            ),
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                side_effect=lambda branch: "a" * 40 if branch == "feature/lin-001" else None,
            ),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=None),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/12\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        run_gh.assert_not_called()
        review_pr = bus_state["tasks"]["LIN-001"]["review_pr"]
        self.assertEqual(review_pr["state"], "missing_pr")
        self.assertIsNone(review_pr["number"])

    def test_missing_pr_is_rechecked_and_adopted_after_publisher_creates_it(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }
        task = {
            "id": "ODP-PR-OWNER-001",
            "title": "Single PR publisher",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": [],
            "next": "ready",
        }
        branch = "task/ODP-PR-OWNER-001"
        found = {"number": 812, "url": "https://example.test/pull/812"}
        bus_state = {"tasks": {}}
        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", side_effect=[None, found]),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(github_bus, "edit_pull_request_rest") as edit,
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            first = github_bus.upsert_review_pr(
                config, bus_state, {"tasks": []}, "o/r", task
            )
            second = github_bus.upsert_review_pr(
                config, bus_state, {"tasks": []}, "o/r", task
            )

        self.assertTrue(first)
        self.assertTrue(second)
        run_gh.assert_not_called()
        edit.assert_called_once()
        self.assertEqual(bus_state["tasks"][task["id"]]["review_pr"]["state"], "open")
        self.assertEqual(bus_state["tasks"][task["id"]]["review_pr"]["number"], 812)

    def test_closed_review_bus_pr_is_reconciled_to_current_open_pr(self) -> None:
        """A stale closed PR must not suppress adoption of the publisher PR."""

        config = {
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }
        task = {
            "id": "ODP-PR-STALE-REF-001",
            "title": "Reconcile stale review reference",
            "summary_zh": "reconcile me",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Codex",
            "depends_on": [],
            "artifacts": [],
            "next": "ready",
        }
        branch = "task/ODP-PR-STALE-REF-001"
        title = f"[ReviewBus] {task['id']} {task['title']}"
        body = "body\n"
        pr_hash = json.dumps(
            {
                "title": title,
                "body": body,
                "labels": ["pantheon-review"],
                "branch": branch,
                "base": "dev",
                "head_sha": "a" * 40,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        bus_state = {
            "tasks": {
                task["id"]: {
                    "review_pr": {
                        "number": 1018,
                        "url": "https://example.test/pull/1018",
                        "branch": branch,
                        "state": "closed",
                    },
                    "last_review_hash": pr_hash,
                }
            }
        }
        found = {"number": 1019, "url": "https://example.test/pull/1019"}

        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=found) as find,
            mock.patch.object(github_bus, "build_template_body", return_value=body),
            mock.patch.object(github_bus, "edit_pull_request_rest") as edit,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, {"tasks": []}, "o/r", task)

        self.assertTrue(changed)
        find.assert_called_once_with("o/r", task["id"], branch, "dev")
        self.assertEqual(edit.call_args.args[1], 1019)
        review_pr = bus_state["tasks"][task["id"]]["review_pr"]
        self.assertEqual(review_pr["number"], 1019)
        self.assertEqual(review_pr["state"], "open")

    def test_skipped_no_commits_is_suppressed_on_consecutive_poll(self) -> None:
        """skipped_no_commits must not trigger re-evaluation when the hash matches.

        Regression: the original ``state != "open"`` guard treated every
        non-open state as stale, so ``skipped_no_commits`` forced
        ``branch_has_diff`` to run again and appended a duplicate
        ``github_review_pr_skipped`` log entry on every poll cycle (~30 s).
        """

        config = {
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }
        task = {
            "id": "ODP-SKIP-NO-COMMITS-001",
            "title": "Suppress no-commit skip",
            "summary_zh": "suppress me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": [],
            "next": "ready",
        }
        branch = "task/ODP-SKIP-NO-COMMITS-001"
        head_sha = "b" * 40
        body = "body\n"
        title = f"[ReviewBus] {task['id']} {task['title']}"
        pr_hash = json.dumps(
            {
                "title": title,
                "body": body,
                "labels": ["pantheon-review"],
                "branch": branch,
                "base": "dev",
                "head_sha": head_sha,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        bus_state = {
            "tasks": {
                task["id"]: {
                    "review_pr": {
                        "number": None,
                        "url": None,
                        "title": title,
                        "branch": branch,
                        "state": "skipped_no_commits",
                        "head_sha": head_sha,
                        "remote_ref": f"refs/heads/{branch}",
                    },
                    "last_review_hash": pr_hash,
                }
            }
        }

        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value=head_sha),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=head_sha),
            mock.patch.object(github_bus, "branch_has_diff", return_value=False) as diff,
            mock.patch.object(github_bus, "build_template_body", return_value=body),
            mock.patch.object(github_bus, "write_activity_log") as log,
        ):
            changed = github_bus.upsert_review_pr(
                config, bus_state, {"tasks": []}, "o/r", task
            )

        # The suppression guard must fire and return False immediately; neither
        # branch_has_diff nor write_activity_log should be called.
        self.assertFalse(changed)
        diff.assert_not_called()
        log.assert_not_called()

    def test_upsert_review_pr_skips_unpublished_remote_branch(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "master",
                "labels": {"review": ["pantheon-bus", "pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }
        bus_state = {"tasks": {}}
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": ["foo.md"],
            "next": "ready for review",
        }

        with (
            mock.patch.object(github_bus, "branch_exists", side_effect=lambda branch: branch == "feature/lin-001"),
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "remote_branch_exists", return_value=False),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        entry = bus_state["tasks"]["LIN-001"]["review_pr"]
        self.assertEqual(entry["state"], "skipped_unpublished_branch")
        self.assertEqual(entry["branch"], "feature/lin-001")
        self.assertEqual(entry["head_sha"], "abc123")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "github_review_pr_skipped")

    def test_upsert_review_pr_rechecks_origin_even_for_recent_unpublished_branch(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "master",
                "unpublished_branch_recheck_seconds": 300,
            }
        }
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        skip_hash = '{"base": "master", "branch": "feature/lin-001", "head_sha": "abc123", "state": "skipped_unpublished_branch", "task_id": "LIN-001"}'
        bus_state = {
            "tasks": {
                "LIN-001": {
                    "review_pr": {
                        "title": "[ReviewBus] LIN-001 Lineage task",
                        "branch": "feature/lin-001",
                        "state": "skipped_unpublished_branch",
                        "head_sha": "abc123",
                        "last_remote_branch_check_at": github_bus.utc_now(),
                    },
                    "last_review_hash": skip_hash,
                }
            }
        }

        with (
            mock.patch.object(github_bus, "branch_exists", side_effect=lambda branch: branch == "feature/lin-001"),
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(
                github_bus,
                "remote_branch_exists",
                side_effect=lambda branch: branch == "feature/lin-001",
            ),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None) as remote_branch_head_sha,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_head_sha.assert_called_once_with("feature/lin-001")

    def test_upsert_review_pr_rechecks_unpublished_branch_after_ttl(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "master",
                "unpublished_branch_recheck_seconds": 300,
            }
        }
        status = {
            "agents": [{"name": "Codex", "branch": "feature/lin-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
        }
        skip_hash = '{"base": "master", "branch": "feature/lin-001", "head_sha": "abc123", "state": "skipped_unpublished_branch", "task_id": "LIN-001"}'
        bus_state = {
            "tasks": {
                "LIN-001": {
                    "review_pr": {
                        "title": "[ReviewBus] LIN-001 Lineage task",
                        "branch": "feature/lin-001",
                        "state": "skipped_unpublished_branch",
                        "head_sha": "abc123",
                        "last_remote_branch_check_at": "2026-04-22T00:00:00Z",
                    },
                    "last_review_hash": skip_hash,
                }
            }
        }

        with (
            mock.patch.object(github_bus, "branch_exists", side_effect=lambda branch: branch == "feature/lin-001"),
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(
                github_bus,
                "remote_branch_exists",
                side_effect=lambda branch: branch == "feature/lin-001",
            ),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None) as remote_branch_head_sha,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_head_sha.assert_called_once_with("feature/lin-001")

    def test_remote_branch_head_sha_requires_exact_origin_ref(self) -> None:
        branch = "task/ODP-REMOTE-001"
        exact_sha = "1" * 40
        heads = {f"{branch}-SIDECAR": "2" * 40, branch: exact_sha}

        with mock.patch.object(github_bus, "remote_branch_heads", return_value=heads) as snapshot:
            self.assertEqual(github_bus.remote_branch_head_sha(branch), exact_sha)

        snapshot.assert_called_once_with("origin")

    def test_upsert_review_pr_uses_task_origin_ref_when_status_root_and_owner_branch_differ(self) -> None:
        task_id = "ODP-API-HEALTH-DATA-MODE-CONTRACT-001"
        task_branch = f"task/{task_id}"
        remote_sha = "6b4d56e8" + "0" * 32
        config = {
            "branch_workflow": {"task_branch_prefix": "task/"},
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
        }
        status = {
            "agents": [{"name": "Antigravity", "branch": "task/ODP-RUNTIME-GCP-001"}],
            "tasks": [],
        }
        task = {
            "id": task_id,
            "title": "Health data mode contract",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "depends_on": [],
            "artifacts": ["foo.py"],
            "next": "ready for review",
        }

        def remote_sha_for(branch: str, remote: str = "origin") -> str | None:
            del remote
            return remote_sha if branch == task_branch else None

        bus_state = {"tasks": {}}
        with (
            mock.patch.object(github_bus, "remote_branch_head_sha", side_effect=remote_sha_for),
            mock.patch.object(github_bus, "branch_exists", return_value=False),
            mock.patch.object(github_bus, "branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "current_branch", return_value="dev"),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True) as branch_has_diff,
            mock.patch.object(github_bus, "find_existing_pr", return_value=None),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/573\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                status,
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        run_gh.assert_not_called()
        branch_has_diff.assert_called_once_with("dev", task_branch, expected_head_sha=remote_sha)
        review_pr = bus_state["tasks"][task_id]["review_pr"]
        self.assertEqual(review_pr["state"], "missing_pr")
        self.assertEqual(review_pr["head_sha"], remote_sha)
        self.assertEqual(review_pr["remote_ref"], f"refs/heads/{task_branch}")

    def test_upsert_review_pr_does_not_skip_when_exact_origin_sha_is_not_fetched(self) -> None:
        task_id = "ODP-UNFETCHED-001"
        task_branch = f"task/{task_id}"
        remote_sha = "7" * 40
        config = {
            "branch_workflow": {"task_branch_prefix": "task/"},
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
        }
        task = {
            "id": task_id,
            "title": "Unfetched task branch",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude3",
            "depends_on": [],
            "artifacts": ["foo.py"],
            "next": "ready for review",
        }
        bus_state = {"tasks": {}}

        with (
            mock.patch.object(
                github_bus,
                "remote_branch_head_sha",
                side_effect=lambda branch, remote="origin": remote_sha if branch == task_branch else None,
            ),
            mock.patch.object(github_bus, "branch_exists", return_value=False),
            mock.patch.object(github_bus, "branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "current_branch", return_value="dev"),
            mock.patch.object(github_bus, "branch_has_diff", return_value=None) as branch_has_diff,
            mock.patch.object(github_bus, "find_existing_pr", return_value=None),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/580\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                {"agents": [], "tasks": []},
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        branch_has_diff.assert_called_once_with("dev", task_branch, expected_head_sha=remote_sha)
        run_gh.assert_not_called()
        self.assertEqual(bus_state["tasks"][task_id]["review_pr"]["state"], "missing_pr")

    def test_upsert_review_pr_recovers_false_unpublished_state_from_task_origin_ref(self) -> None:
        task_id = "ODP-API-HEALTH-DATA-MODE-CONTRACT-001"
        task_branch = f"task/{task_id}"
        remote_sha = "6b4d56e8" + "0" * 32
        config = {
            "branch_workflow": {"task_branch_prefix": "task/"},
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
        }
        status = {
            "agents": [{"name": "Antigravity", "branch": "task/ODP-RUNTIME-GCP-001"}],
            "tasks": [],
        }
        task = {
            "id": task_id,
            "title": "Health data mode contract",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "depends_on": [],
            "artifacts": ["foo.py"],
            "next": "ready for review",
        }
        skip_hash = json.dumps(
            {
                "state": "skipped_unpublished_branch",
                "task_id": task_id,
                "branch": task_branch,
                "base": "dev",
                "head_sha": None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        bus_state = {
            "tasks": {
                task_id: {
                    "review_pr": {
                        "branch": task_branch,
                        "state": "skipped_unpublished_branch",
                        "head_sha": None,
                        "last_remote_branch_check_at": "2026-08-02T08:00:00Z",
                    },
                    "last_review_hash": skip_hash,
                }
            }
        }

        def remote_sha_for(branch: str, remote: str = "origin") -> str | None:
            del remote
            return remote_sha if branch == task_branch else None

        with (
            mock.patch.object(github_bus, "remote_branch_head_sha", side_effect=remote_sha_for),
            mock.patch.object(github_bus, "branch_exists", return_value=False),
            mock.patch.object(github_bus, "branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "current_branch", return_value="dev"),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=None),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/573\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(
                config,
                bus_state,
                status,
                "ajoe734/pantheon",
                task,
            )

        self.assertTrue(changed)
        run_gh.assert_not_called()
        self.assertEqual(bus_state["tasks"][task_id]["review_pr"]["state"], "missing_pr")
        review_pr = bus_state["tasks"][task_id]["review_pr"]
        self.assertEqual(review_pr["state"], "missing_pr")
        self.assertEqual(review_pr["head_sha"], remote_sha)
        self.assertEqual(review_pr["remote_ref"], f"refs/heads/{task_branch}")


class FindExistingReviewPrTests(unittest.TestCase):
    """PRs opened by workers carry no [ReviewBus] prefix and must still be found."""

    WORKER_PR = {
        "number": 554,
        "title": "ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE: support acceptance packet",
        "url": "https://github.com/alfloop-dev/odayplus/pull/554",
        "headRefName": "task/ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE",
        "baseRefName": "dev",
        "state": "OPEN",
    }

    def test_finds_a_worker_opened_pr_by_head_branch(self) -> None:
        """The regression: 2674 failed creations against a PR that was already open."""

        branch = "task/ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE"

        def fake_gh_json(args: list[str]) -> list[dict]:
            # The title search is what used to run, and it matches nothing here.
            if "--search" in args:
                return []
            return [self.WORKER_PR]

        with mock.patch.object(github_bus, "gh_json", side_effect=fake_gh_json) as gh_json:
            found = github_bus.find_existing_pr(
                "alfloop-dev/odayplus", "ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE", branch
            )

        self.assertIsNotNone(found)
        self.assertEqual(found["number"], 554)
        # The branch lookup must come first and carry no title filter.
        first_args = gh_json.call_args_list[0].args[0]
        self.assertIn("--head", first_args)
        self.assertIn(branch, first_args)
        self.assertNotIn("--search", first_args)

    @staticmethod
    def _fake_gh(prs: list[dict]):
        """Stand in for gh, reproducing the behaviour that matters here.

        `--head` alone filters exactly. Combined with `--search`, gh degrades it
        into a fuzzy `head:` qualifier that also matches branches which merely
        start with the given name -- which is how a parent task reaches its own
        sidecar PRs. The previous fake ignored `--head` entirely and so could not
        express either behaviour.
        """

        def run(args: list[str]) -> list[dict]:
            head = args[args.index("--head") + 1] if "--head" in args else None
            base = args[args.index("--base") + 1] if "--base" in args else None
            searching = "--search" in args
            out = []
            for pr in prs:
                ref = pr.get("headRefName") or ""
                if head is not None:
                    if searching:
                        if not ref.startswith(head):
                            continue
                    elif ref != head:
                        continue
                if base is not None and (pr.get("baseRefName") or "") != base:
                    continue
                out.append(pr)
            return out

        return run

    def test_falls_back_to_the_title_search_when_no_pr_is_open_from_the_branch(self) -> None:
        moved = {
            "number": 777,
            "title": "[ReviewBus] ODP-X something",
            "url": "https://github.com/alfloop-dev/odayplus/pull/777",
            "headRefName": "task/ODP-X-renamed",
            "baseRefName": "dev",
            "state": "OPEN",
        }

        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([moved])):
            found = github_bus.find_existing_pr("alfloop-dev/odayplus", "ODP-X", "task/ODP-X")

        self.assertEqual(found["number"], 777)

    def test_a_parent_task_never_adopts_its_sidecars_pr(self) -> None:
        """The fuzzy-head regression: this would retitle and overwrite PR 639.

        Reproduced live against the real repo -- searching for the parent id with
        `--head task/<parent>` returns the parent's PR 621 *and* sidecar PR 639.
        The fallback only runs when the parent branch has no open PR, so the
        sidecar was first in the list and would have been adopted.
        """

        parent = "ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001"
        sidecar_pr = {
            "number": 639,
            "title": f"[ReviewBus] {parent}-SIDECAR-REVIEW anchor review packet",
            "url": "https://github.com/alfloop-dev/odayplus/pull/639",
            "headRefName": f"task/{parent}-SIDECAR-REVIEW",
            "baseRefName": "dev",
            "state": "OPEN",
        }

        # The parent's own branch has no open PR; only the sidecar does.
        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([sidecar_pr])):
            found = github_bus.find_existing_pr("alfloop-dev/odayplus", parent, f"task/{parent}")

        self.assertIsNone(found)

        # And the sidecar task itself still finds its own PR.
        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([sidecar_pr])):
            own = github_bus.find_existing_pr(
                "alfloop-dev/odayplus", f"{parent}-SIDECAR-REVIEW", f"task/{parent}-SIDECAR-REVIEW"
            )

        self.assertEqual(own["number"], 639)

    def test_returns_none_when_nothing_matches(self) -> None:
        with mock.patch.object(github_bus, "gh_json", return_value=[]):
            self.assertIsNone(github_bus.find_existing_pr("repo", "ODP-X", "task/ODP-X"))

    # -- base filtering ---------------------------------------------------
    #
    # One head branch can carry several open PRs when their bases differ, so
    # `--head` alone is not the (head, base) key GitHub actually enforces.

    PROMOTION_PR = {
        "number": 621,
        "title": "[ReviewBus] ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001 Resolve detached-HEAD branch resolution",
        "url": "https://github.com/alfloop-dev/odayplus/pull/621",
        "headRefName": "task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001",
        "baseRefName": "main",
        "state": "OPEN",
    }

    def test_a_pr_aimed_at_another_base_is_not_adopted(self) -> None:
        """The strand: PR 621 shares the task's head branch but targets `main`.

        Its content had already merged into `dev` via PR 616, so the task had
        nothing left to publish -- yet head-only discovery kept handing the bus a
        PR aimed at the promotion branch. The review gate, auto-merge, and the
        `done` ancestor check all reason about the task base, so the task could
        never leave the repair loop.
        """

        task_id = "ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001"
        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([self.PROMOTION_PR])):
            found = github_bus.find_existing_pr(
                "alfloop-dev/odayplus", task_id, f"task/{task_id}", "dev"
            )

        self.assertIsNone(found)

    def test_the_head_lookup_asks_github_for_the_task_pr_base(self) -> None:
        with mock.patch.object(github_bus, "gh_json", return_value=[]) as gh_json:
            github_bus.find_existing_pr("alfloop-dev/odayplus", "ODP-X", "task/ODP-X", "dev")

        head_args = gh_json.call_args_list[0].args[0]
        self.assertIn("--base", head_args)
        self.assertEqual(head_args[head_args.index("--base") + 1], "dev")

    def test_the_base_filter_is_reapplied_client_side(self) -> None:
        """A gh build that ignores `--base` must not reintroduce the bug."""

        def gh_ignoring_base(args: list[str]) -> list[dict]:
            return [] if "--search" in args else [self.PROMOTION_PR]

        with mock.patch.object(github_bus, "gh_json", side_effect=gh_ignoring_base):
            found = github_bus.find_existing_pr(
                "alfloop-dev/odayplus",
                "ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001",
                "task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001",
                "dev",
            )

        self.assertIsNone(found)

    def test_the_title_fallback_also_rejects_a_foreign_base(self) -> None:
        """The fallback runs precisely when the branch has no PR on the task base."""

        task_id = "ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001"
        moved = dict(self.PROMOTION_PR, headRefName="task/renamed")

        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([moved])):
            self.assertIsNone(
                github_bus.find_existing_pr("alfloop-dev/odayplus", task_id, f"task/{task_id}", "dev")
            )

        # Same PR, retargeted at the task base: now it is this task's review PR.
        with mock.patch.object(
            github_bus, "gh_json", side_effect=self._fake_gh([dict(moved, baseRefName="dev")])
        ):
            found = github_bus.find_existing_pr(
                "alfloop-dev/odayplus", task_id, f"task/{task_id}", "dev"
            )

        self.assertEqual(found["number"], 621)

    def test_an_omitted_base_keeps_the_previous_behaviour(self) -> None:
        with mock.patch.object(github_bus, "gh_json", side_effect=self._fake_gh([self.PROMOTION_PR])) as gh_json:
            found = github_bus.find_existing_pr(
                "alfloop-dev/odayplus",
                "ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001",
                "task/ODP-ORCH-DETACHED-HEAD-BRANCH-RESOLUTION-001",
            )

        self.assertEqual(found["number"], 621)
        self.assertNotIn("--base", gh_json.call_args_list[0].args[0])

    def test_upsert_review_pr_scopes_discovery_to_the_task_pr_base(self) -> None:
        """The wiring: the base upsert already computed must reach discovery."""

        config = {
            "github_bus": {
                "default_branch": "master",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
            "branch_workflow": {"enabled": True, "task_pr": {"target_branch": "dev"}},
        }
        task = {
            "id": "ODP-PR-BASE-001",
            "title": "Base-scoped discovery",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": [],
            "next": "ready",
        }
        branch = "task/ODP-PR-BASE-001"
        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=None) as find,
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            github_bus.upsert_review_pr(config, {"tasks": {}}, {"tasks": []}, "o/r", task)

        find.assert_called_once_with("o/r", task["id"], branch, "dev")

class GitHubBusProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        github_bus.clear_remote_branch_snapshot_cache()

    def test_edit_pull_request_uses_rest_without_projects_classic_graphql(self) -> None:
        with mock.patch.object(github_bus, "run_gh") as run_gh:
            github_bus.edit_pull_request_rest(
                "alfloop-dev/odayplus",
                593,
                "[ReviewBus] TASK Review",
                "body\n",
                ["pantheon-bus", "pantheon-review"],
            )

        self.assertEqual(run_gh.call_count, 2)
        edit_args = run_gh.call_args_list[0].args[0]
        self.assertEqual(edit_args[:4], ["api", "--method", "PATCH", "repos/alfloop-dev/odayplus/pulls/593"])
        self.assertIn("title=[ReviewBus] TASK Review", edit_args)
        self.assertIn("body=body\n", edit_args)
        label_args = run_gh.call_args_list[1].args[0]
        self.assertEqual(label_args[:4], ["api", "--method", "POST", "repos/alfloop-dev/odayplus/issues/593/labels"])
        self.assertIn("labels[]=pantheon-bus", label_args)
        self.assertIn("labels[]=pantheon-review", label_args)

    def test_run_gh_process_kills_process_group_on_timeout(self) -> None:
        class FakePopen:
            def __init__(self) -> None:
                self.pid = 4321
                self.returncode = None
                self.wait_calls: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                self.wait_calls.append(timeout)
                raise subprocess.TimeoutExpired(cmd=["gh", "api"], timeout=timeout)

        fake_process = FakePopen()

        with (
            mock.patch.object(github_bus.subprocess, "Popen", return_value=fake_process),
            mock.patch.object(github_bus.os, "killpg") as killpg,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                github_bus.run_gh_process(["api", "repos/ajoe734/pantheon/issues/4/comments"], timeout_seconds=1.0)

        killpg.assert_called_once_with(4321, github_bus.signal.SIGKILL)
        self.assertEqual(fake_process.wait_calls, [1.0, 0.2])

    def test_remote_branch_probe_times_out_without_blocking_the_bus(self) -> None:
        with mock.patch.object(
            github_bus,
            "run_git_network_process",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=8),
        ) as run_git_network_process:
            self.assertFalse(github_bus.remote_branch_exists("task/ODP-REMOTE-001"))

        self.assertEqual(
            run_git_network_process.call_args.args[0],
            ["ls-remote", "--heads", "origin"],
        )

    def test_remote_branch_snapshot_reuses_one_probe_for_multiple_branches(self) -> None:
        proc = subprocess.CompletedProcess(
            ["git", "ls-remote"],
            0,
            "abc\trefs/heads/task/ODP-ONE-001\ndef\trefs/heads/task/ODP-TWO-001\n",
            "",
        )
        with mock.patch.object(github_bus, "run_git_network_process", return_value=proc) as probe:
            self.assertTrue(github_bus.remote_branch_exists("task/ODP-ONE-001"))
            self.assertTrue(github_bus.remote_branch_exists("task/ODP-TWO-001"))
            self.assertFalse(github_bus.remote_branch_exists("task/ODP-MISSING-001"))

        probe.assert_called_once_with(
            ["ls-remote", "--heads", "origin"],
            timeout_seconds=mock.ANY,
        )

    def test_parse_remote_head_names_ignores_malformed_and_non_head_refs(self) -> None:
        self.assertEqual(
            github_bus.parse_remote_head_names(
                "abc\trefs/heads/task/ODP-ONE-001\n"
                "\n"
                "def\n"
                "ghi\trefs/tags/v1.2.3\n"
                "jkl\trefs/heads/dev\n"
            ),
            frozenset({"task/ODP-ONE-001", "dev"}),
        )

    def _snapshot_probe_failure(
        self, *, elapsed: float, failure: subprocess.TimeoutExpired | subprocess.CompletedProcess[str]
    ) -> bool:
        """Seed a good snapshot at t=0, then re-probe at t=elapsed with a failure."""
        good = subprocess.CompletedProcess(
            ["git", "ls-remote"], 0, "abc\trefs/heads/task/ODP-ONE-001\n", ""
        )
        kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
        with (
            mock.patch.object(github_bus.time, "monotonic", side_effect=[0.0, elapsed]),
            mock.patch.object(github_bus, "remote_branch_snapshot_ttl_seconds", return_value=30.0),
            mock.patch.object(github_bus, "remote_branch_snapshot_max_stale_seconds", return_value=300.0),
            mock.patch.object(github_bus, "git_network_timeout_seconds", return_value=8.0),
        ):
            with mock.patch.object(github_bus, "run_git_network_process", return_value=good):
                self.assertTrue(github_bus.remote_branch_exists("task/ODP-ONE-001"))
            with mock.patch.object(github_bus, "run_git_network_process", **kwargs):
                return github_bus.remote_branch_exists("task/ODP-ONE-001")

    def test_failed_probe_serves_last_good_snapshot_instead_of_empty(self) -> None:
        # A timeout says nothing about the remote's branches. Caching an empty
        # set would report a published branch as unpublished, which freezes that
        # task for unpublished_branch_recheck_seconds -- far past the outage.
        self.assertTrue(
            self._snapshot_probe_failure(
                elapsed=100.0,
                failure=subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=8),
            )
        )

    def test_failed_probe_with_nonzero_exit_serves_last_good_snapshot(self) -> None:
        self.assertTrue(
            self._snapshot_probe_failure(
                elapsed=100.0,
                failure=subprocess.CompletedProcess(["git", "ls-remote"], 128, "", "fatal: remote error"),
            )
        )

    def test_failed_probe_past_max_stale_window_fails_closed(self) -> None:
        self.assertFalse(
            self._snapshot_probe_failure(
                elapsed=500.0,
                failure=subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=8),
            )
        )

    def test_first_probe_failure_without_snapshot_fails_closed(self) -> None:
        proc = subprocess.CompletedProcess(["git", "ls-remote"], 128, "", "fatal: remote error")
        with mock.patch.object(github_bus, "run_git_network_process", return_value=proc):
            self.assertFalse(github_bus.remote_branch_exists("task/ODP-ONE-001"))

    def test_snapshot_within_ttl_is_served_without_reloading_config(self) -> None:
        proc = subprocess.CompletedProcess(
            ["git", "ls-remote"], 0, "abc\trefs/heads/task/ODP-ONE-001\n", ""
        )
        with (
            mock.patch.object(github_bus.time, "monotonic", side_effect=[0.0, 5.0]),
            mock.patch.object(
                github_bus, "remote_branch_snapshot_ttl_seconds", return_value=30.0
            ) as ttl,
            mock.patch.object(github_bus, "run_git_network_process", return_value=proc) as probe,
        ):
            self.assertTrue(github_bus.remote_branch_exists("task/ODP-ONE-001"))
            self.assertTrue(github_bus.remote_branch_exists("task/ODP-ONE-001"))

        probe.assert_called_once()
        ttl.assert_called_once()

    def test_run_git_network_process_kills_process_group_on_timeout(self) -> None:
        class FakePopen:
            def __init__(self) -> None:
                self.pid = 5678
                self.returncode = None
                self.wait_calls: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                self.wait_calls.append(timeout)
                raise subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=timeout)

        fake_process = FakePopen()
        with (
            mock.patch.object(github_bus.subprocess, "Popen", return_value=fake_process),
            mock.patch.object(github_bus.os, "killpg") as killpg,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                github_bus.run_git_network_process(
                    ["ls-remote", "--heads", "origin", "task/ODP-REMOTE-001"],
                    timeout_seconds=1.0,
                )

        killpg.assert_called_once_with(5678, github_bus.signal.SIGKILL)
        self.assertEqual(fake_process.wait_calls, [1.0, 0.2])

    def test_run_gh_uses_vendored_wrapper_when_system_gh_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            vendored = root / ".orchestrator" / "bin" / "gh"
            vendored.parent.mkdir(parents=True, exist_ok=True)
            vendored.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            vendored.chmod(0o755)

            with (
                mock.patch.object(github_bus, "ROOT", root),
                # The rule now lives in common.resolve_github_cli, so "no system
                # gh" is simulated at its seams, not at github_bus.command_exists.
                mock.patch.object(common.shutil, "which", return_value=None),
                mock.patch.object(common, "SYSTEM_GH_PATHS", ()),
                mock.patch.object(
                    github_bus,
                    "run_gh_process",
                    return_value=subprocess.CompletedProcess([str(vendored), "auth", "status"], 0, "", ""),
                ) as run_gh_process,
            ):
                github_bus.run_gh(["auth", "status"], allow_offline=False)

            self.assertEqual(run_gh_process.call_args.kwargs["gh_binary"], str(vendored))


class GitHubCoordinationCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.pantheon = root / "pantheon"
        (self.pantheon / "docs-site").mkdir(parents=True, exist_ok=True)
        (self.pantheon / "ai-status.json").write_text('{"tasks":[],"handoffs":[]}\n', encoding="utf-8")
        (self.pantheon / "current-work.md").write_text("# current work\n", encoding="utf-8")
        (self.pantheon / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
        (self.pantheon / "docs-site" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        self.config = {
            "paths": {
                "status_file": str(self.pantheon / "ai-status.json"),
                "activity_log": str(self.pantheon / "ai-activity-log.jsonl"),
                "current_work": str(self.pantheon / "current-work.md"),
                "dashboard": str(self.pantheon / "docs-site" / "index.html"),
                "event_queue": str(self.pantheon / ".orchestrator" / "event-queue.jsonl"),
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude", "adapter": "claude_cli"},
            },
            "coordination": {
                "enabled": True,
                "worker_routes": {
                    "pantheon-bff-worker": {"target_agent": "Codex"},
                    "engine-worker": {"target_agent": "Claude", "requires_human_approval": True},
                },
            },
        }
        self.bus_state = {"tasks": {}, "coordination": {}}
        self.status = {"tasks": []}

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_dispatch_command_queues_coordination_event(self) -> None:
        command = GitHubCommand(verb="dispatch", target="pantheon-bff", raw="/dispatch pantheon-bff F-042", args=("pantheon-bff", "F-042"))
        changed, reply = github_bus.apply_bus_command(
            self.config,
            self.bus_state,
            self.status,
            "ajoe734/pantheon",
            command,
            "ajoe734",
            runtime_state={"coordination": {"features": {"F-042": {"feature_id": "F-042"}}}},
        )

        self.assertTrue(changed)
        self.assertEqual(reply, "Queued `pantheon-bff-worker` for `F-042`.")
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "pantheon-bff-worker")

    def test_approve_engine_command_bypasses_manual_gate(self) -> None:
        command = GitHubCommand(verb="approve-engine", target="F-042", raw="/approve-engine F-042", args=("F-042",))
        changed, reply = github_bus.apply_bus_command(
            self.config,
            self.bus_state,
            self.status,
            "ajoe734/pantheon",
            command,
            "ajoe734",
            runtime_state={"coordination": {"features": {"F-042": {"feature_id": "F-042"}}}},
        )

        self.assertTrue(changed)
        self.assertEqual(reply, "Queued engine worker for `F-042`.")
        queue = load_jsonl(Path(self.config["paths"]["event_queue"]))
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["metadata"]["coordination"]["worker_kind"], "engine-worker")


class TaskPRDiscoveryTests(unittest.TestCase):
    def test_review_branch_for_task_prioritizes_immutable_task_ref_over_unrelated_agent_branch(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Antigravity",
                    "branch": "task/ODP-RUNTIME-GCP-001",
                }
            ]
        }
        task = {
            "id": "ODP-API-HEALTH-DATA-MODE-CONTRACT-001",
            "owner": "Antigravity",
            "title": "Health data mode contract",
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name in {
                "task/ODP-RUNTIME-GCP-001",
                "task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001",
            }

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(found_branch, "task/ODP-API-HEALTH-DATA-MODE-CONTRACT-001")

    def test_review_branch_for_task_rejects_related_sidecar_agent_branch(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Codex",
                    "branch": "task/ODP-FOO-001-SIDECAR-ACCEPTANCE",
                }
            ]
        }
        task = {
            "id": "ODP-FOO-001",
            "owner": "Codex",
            "title": "Parent task",
            "github": {"head_branch": "task/ODP-FOO-001-SIDECAR-ACCEPTANCE"},
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name in {
                "task/ODP-FOO-001",
                "task/ODP-FOO-001-SIDECAR-ACCEPTANCE",
            }

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(found_branch, "task/ODP-FOO-001")
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "origin/task/ODP-FOO-001"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001-SIDECAR-ACCEPTANCE"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-0010"))

    def test_review_branch_for_task_rejects_unrelated_agent_or_current_branch_when_canonical_absent(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Codex",
                    "branch": "feat/unrelated-branch",
                }
            ]
        }
        task = {
            "id": "ODP-FOO-001",
            "owner": "Codex",
            "title": "Parent task with missing canonical branch",
        }

        queried_branches: list[str] = []

        def mock_exists(branch_name: str) -> bool:
            queried_branches.append(branch_name)
            return branch_name == "feat/unrelated-branch"

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            with mock.patch.object(github_bus, "current_branch", return_value="feat/unrelated-branch"):
                found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertIsNone(found_branch)
        self.assertIn("task/ODP-FOO-001", queried_branches)

    def test_review_branch_for_task_returns_none_when_canonical_absent_and_only_sidecar_or_unrelated_branch_exists(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Codex",
                    "branch": "task/ODP-FOO-001-SIDECAR-ACCEPTANCE",
                }
            ]
        }
        task = {
            "id": "ODP-FOO-001",
            "owner": "Codex",
            "title": "Parent task with missing canonical branch",
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name in {
                "task/ODP-FOO-001-SIDECAR-ACCEPTANCE",
                "feat/unrelated-branch",
            }

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            with mock.patch.object(github_bus, "current_branch", return_value="task/ODP-FOO-001-SIDECAR-ACCEPTANCE"):
                found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertIsNone(found_branch)

    def test_review_branch_for_task_accepts_exact_matching_agent_branch_when_canonical_prefix_absent(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Codex",
                    "branch": "custom-prefix/ODP-FOO-001",
                }
            ]
        }
        task = {
            "id": "ODP-FOO-001",
            "owner": "Codex",
            "title": "Parent task with custom prefix branch",
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name == "custom-prefix/ODP-FOO-001"

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(found_branch, "custom-prefix/ODP-FOO-001")

    def test_branch_ref_resolution_supports_remote_tracking_refs(self) -> None:
        def mock_cmd(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
            cmd_str = " ".join(cmd)
            if "show-ref" in cmd_str and "refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "sha123 refs/remotes/origin/task/ODP-REMOTE-001\n", "")
            if "rev-parse origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "70ea2d817c1d60db346869a0b284a6942fe78d2a\n", "")
            if "rev-list --count" in cmd_str and "origin/dev..origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "3\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "ref not found")

        with mock.patch.object(github_bus, "run_command", side_effect=mock_cmd):
            self.assertTrue(github_bus.branch_exists("task/ODP-REMOTE-001"))
            self.assertEqual(github_bus.branch_head_sha("task/ODP-REMOTE-001"), "70ea2d817c1d60db346869a0b284a6942fe78d2a")
            self.assertTrue(github_bus.branch_has_diff("dev", "task/ODP-REMOTE-001"))

    def test_branch_ref_resolution_prefers_coherent_remote_refs_over_stale_local_refs(self) -> None:
        calls: list[str] = []

        def mock_cmd(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
            cmd_str = " ".join(cmd)
            calls.append(cmd_str)
            if "rev-parse refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "2222222222222222222222222222222222222222\n", "")
            if "rev-parse task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "1111111111111111111111111111111111111111\n", "")
            if "rev-list --count refs/remotes/origin/dev..refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "3\n", "")
            if "rev-list --count dev..task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "ref not found")

        with mock.patch.object(github_bus, "run_command", side_effect=mock_cmd):
            self.assertEqual(
                github_bus.branch_head_sha("task/ODP-REMOTE-001"),
                "2222222222222222222222222222222222222222",
            )
            self.assertTrue(github_bus.branch_has_diff("dev", "task/ODP-REMOTE-001"))

        self.assertNotIn("git rev-parse task/ODP-REMOTE-001", calls)
        self.assertNotIn("git rev-list --count dev..task/ODP-REMOTE-001", calls)

    def test_branch_diff_is_unknown_when_exact_origin_sha_is_not_fetched(self) -> None:
        expected_sha = "3" * 40
        stale_sha = "2" * 40
        calls: list[str] = []

        def mock_cmd(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
            del cwd
            cmd_str = " ".join(cmd)
            calls.append(cmd_str)
            if "rev-parse refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, f"{stale_sha}\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "ref not found")

        with mock.patch.object(github_bus, "run_command", side_effect=mock_cmd):
            self.assertIsNone(
                github_bus.branch_has_diff(
                    "dev",
                    "task/ODP-REMOTE-001",
                    expected_head_sha=expected_sha,
                )
            )

        self.assertFalse(any("rev-list --count" in call for call in calls))

    def test_branch_diff_uses_ref_only_when_it_matches_exact_origin_sha(self) -> None:
        expected_sha = "3" * 40

        def mock_cmd(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
            del cwd
            cmd_str = " ".join(cmd)
            if "rev-parse refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, f"{expected_sha}\n", "")
            if "rev-list --count refs/remotes/origin/dev..refs/remotes/origin/task/ODP-REMOTE-001" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, "2\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "ref not found")

        with mock.patch.object(github_bus, "run_command", side_effect=mock_cmd):
            self.assertTrue(
                github_bus.branch_has_diff(
                    "dev",
                    "task/ODP-REMOTE-001",
                    expected_head_sha=expected_sha,
                )
            )

    def test_current_branch_returns_none_when_detached_head(self) -> None:
        def mock_cmd(cmd: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
            if "symbolic-ref" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "fatal: ref HEAD is not a symbolic ref")
            return subprocess.CompletedProcess(cmd, 0, "HEAD\n", "")

        with mock.patch.object(github_bus, "run_command", side_effect=mock_cmd):
            self.assertIsNone(github_bus.current_branch())

    def test_branch_exists_returns_false_for_head(self) -> None:
        self.assertFalse(github_bus.branch_exists("HEAD"))
        self.assertFalse(github_bus.branch_exists("origin/HEAD"))

    def test_review_branch_for_task_rejects_head_branch_name(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {"agents": [{"name": "Codex", "branch": "HEAD"}]}
        task = {"id": "ODP-FOO-001", "owner": "Codex", "branch": "HEAD"}

        with mock.patch.object(github_bus, "current_branch", return_value=None):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertIsNone(found_branch)


class DetachedHeadBranchResolutionTests(unittest.TestCase):
    """A detached worktree has no branch, and must not claim one.

    `git rev-parse --abbrev-ref HEAD` answers the literal string "HEAD" when
    detached. Every guard downstream accepts it -- it is truthy, it is not the
    default branch, and branch_exists("HEAD") succeeds because HEAD always
    resolves -- so the bus once recorded "HEAD" as a task's review branch.
    """

    def test_detached_head_yields_no_branch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-detached-") as tmp:
            repo = Path(tmp)
            for args in (
                ["git", "init", "-q", str(repo)],
                ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "--allow-empty", "-m", "init"],
                ["git", "-C", str(repo), "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "HEAD"],
            ):
                subprocess.run(args, check=True, capture_output=True)

            with mock.patch.object(github_bus, "ROOT", repo):
                self.assertIsNone(github_bus.current_branch())

    def test_named_branch_is_still_returned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-named-") as tmp:
            repo = Path(tmp)
            for args in (
                ["git", "init", "-q", "-b", "task/ODP-X-001", str(repo)],
                ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "--allow-empty", "-m", "init"],
            ):
                subprocess.run(args, check=True, capture_output=True)

            with mock.patch.object(github_bus, "ROOT", repo):
                self.assertEqual(github_bus.current_branch(), "task/ODP-X-001")
class TaskPRBaseBranchTests(unittest.TestCase):
    def test_task_pr_base_uses_branch_workflow_target_not_repo_default(self) -> None:
        config = {
            "github_bus": {"default_branch": "main"},
            "branch_workflow": {"enabled": True, "dev_branch": "dev", "task_pr": {"target_branch": "dev"}},
        }

        self.assertEqual(github_bus.task_pr_base_branch(config), "dev")
        # The repository default is still reported truthfully for callers that
        # genuinely mean "the default branch".
        self.assertEqual(github_bus.default_branch(config), "main")

    def test_task_pr_base_falls_back_to_dev_branch_when_target_absent(self) -> None:
        config = {
            "github_bus": {"default_branch": "main"},
            "branch_workflow": {"enabled": True, "dev_branch": "dev", "task_pr": {"auto_merge": True}},
        }

        self.assertEqual(github_bus.task_pr_base_branch(config), "dev")

    def test_task_pr_base_falls_back_to_default_when_branch_workflow_disabled(self) -> None:
        config = {
            "github_bus": {"default_branch": "main"},
            "branch_workflow": {"enabled": False, "dev_branch": "dev", "task_pr": {"target_branch": "dev"}},
        }

        self.assertEqual(github_bus.task_pr_base_branch(config), "main")

    def test_upsert_review_pr_missing_message_names_branch_workflow_target(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "main",
                "labels": {"review": ["pantheon-bus", "pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
            "branch_workflow": {"enabled": True, "dev_branch": "dev", "task_pr": {"target_branch": "dev"}},
        }
        bus_state = {"tasks": {}}
        status = {
            "agents": [{"name": "Codex", "branch": "task/LIN-001"}],
            "tasks": [],
        }
        task = {
            "id": "LIN-001",
            "title": "Lineage task",
            "summary_zh": "review me",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": ["foo.md"],
            "next": "ready for review",
        }

        with (
            mock.patch.object(github_bus, "branch_exists", side_effect=lambda branch: branch == "task/LIN-001"),
            mock.patch.object(github_bus, "branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "remote_branch_exists", return_value=True),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value="abc123"),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=None),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    "https://github.com/ajoe734/pantheon/pull/12\n",
                    "",
                ),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log") as activity_log,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        run_gh.assert_not_called()
        event = activity_log.call_args.args[1]
        self.assertEqual(event["type"], "github_review_pr_missing")
        self.assertIn("no open PR against `dev`", event["message"])


class PrBackedStatusCoverageTests(unittest.TestCase):
    """A task approved inside one poll interval must still reconcile its PR.

    Keying PR adoption on `review` alone drops any task that leaves that status
    before the next poll: the supervisor then reads `unknown` CI because no PR
    exists, fails closed on finalize, and nothing ever goes back to create it.
    """

    def _task(self, status: str) -> dict:
        return {
            "id": "ODP-X-001",
            "title": "T",
            "summary_zh": "s",
            "status": status,
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "artifacts": [],
            "next": "n",
        }

    def test_statuses_come_from_config_not_a_literal(self) -> None:
        config = {"ready_dispatcher": {"review_statuses": ["reviewing"], "finalize_statuses": ["approved"]}}

        self.assertEqual(github_bus.pr_backed_statuses(config), {"reviewing", "approved"})

    def test_defaults_cover_review_and_review_approved(self) -> None:
        self.assertEqual(github_bus.pr_backed_statuses({}), {"review", "review_approved"})

    def _sync_and_capture_pr_tasks(self, tasks: list[dict]) -> list[str]:
        config = {
            "github_bus": {"repo": "o/r", "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"}},
        }
        seen: list[str] = []
        with (
            mock.patch.object(github_bus, "pull_commands", return_value=[]),
            mock.patch.object(github_bus, "upsert_review_pr", side_effect=lambda c, b, s, r, t: seen.append(t["id"]) or False),
            mock.patch.object(github_bus, "upsert_ops_issue", return_value=False),
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            github_bus.sync_outbound(config, {"tasks": {}}, {"tasks": tasks, "blockers": []}, {}, "o/r")
        return seen

    def test_review_approved_task_still_gets_a_pr(self) -> None:
        seen = self._sync_and_capture_pr_tasks([self._task("review_approved")])

        self.assertEqual(seen, ["ODP-X-001"])

    def test_review_task_still_gets_a_pr(self) -> None:
        seen = self._sync_and_capture_pr_tasks([self._task("review")])

        self.assertEqual(seen, ["ODP-X-001"])

    def test_unrelated_statuses_are_left_alone(self) -> None:
        tasks = [self._task(s) for s in ("todo", "in_progress", "blocked", "done")]
        for index, task in enumerate(tasks):
            task["id"] = f"ODP-X-{index}"

        self.assertEqual(self._sync_and_capture_pr_tasks(tasks), [])


class ApprovedTaskAutoMergeTests(unittest.TestCase):
    """Approval must arm the PR, or a green PR just sits there while dev moves.

    `ai_status.py` has always printed `autoMerge=enabled|disabled`; nothing ever
    set it. The cost of the gap is re-run CI and a second review of work that
    already passed, every time dev advances under a parked PR.
    """

    TASK_ID = "ODP-ORCH-REVIEWBUS-AUTOMERGE-001"
    BRANCH = "task/ODP-ORCH-REVIEWBUS-AUTOMERGE-001"
    REPO = "alfloop-dev/odayplus"

    def test_pr_publish_and_auto_merge_have_one_owner_each(self) -> None:
        root = Path(github_bus.__file__).resolve().parents[1]
        bus_source = Path(github_bus.__file__).read_text(encoding="utf-8")
        finalize_source = (root / "delivery_toolchain/git/task_finalize.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('["pr", "create"', bus_source)
        self.assertEqual(bus_source.count('run_gh(["pr", "merge"'), 1)
        self.assertNotIn(" --auto", finalize_source)
        self.assertFalse((root / ".orchestrator/auto_merge_green_prs.py").exists())
        self.assertFalse((root / ".orchestrator/auto-merge-guard.sh").exists())

    def _config(self, *, auto_merge: bool = True) -> dict:
        return {
            "github_bus": {"default_branch": "main"},
            "branch_workflow": {
                "enabled": True,
                "dev_branch": "dev",
                "task_pr": {"target_branch": "dev", "auto_merge": auto_merge},
            },
        }

    def _task(self, status: str = "review_approved") -> dict:
        return {
            "id": self.TASK_ID,
            "title": "ReviewBus auto-merge",
            "summary_zh": "s",
            "status": status,
            "owner": "Claude3",
            "reviewer": "Antigravity2",
            "depends_on": [],
            "artifacts": [],
            "next": "n",
        }

    def _bus_state(self, number: int | None = 700) -> dict:
        return {
            "tasks": {
                self.TASK_ID: {
                    "review_pr": {
                        "number": number,
                        "url": f"https://github.com/{self.REPO}/pull/{number}",
                        "branch": self.BRANCH,
                        "state": "open",
                    },
                    "ops_issue": None,
                    "auto_merge": None,
                    "last_review_hash": None,
                    "last_issue_hash": None,
                }
            }
        }

    def _pr(self, **overrides) -> dict:
        pr = {
            "number": 700,
            "state": "OPEN",
            "isDraft": True,
            "autoMergeRequest": None,
            "baseRefName": "dev",
            "headRefName": self.BRANCH,
            "url": f"https://github.com/{self.REPO}/pull/700",
            "mergeStateStatus": "BLOCKED",
        }
        pr.update(overrides)
        return pr

    def _arm(self, pr: dict | None, *, bus_state: dict | None = None, run_gh_side_effect=None):
        bus_state = bus_state if bus_state is not None else self._bus_state()
        with (
            mock.patch.object(github_bus, "gh_json", return_value=pr),
            mock.patch.object(
                github_bus,
                "run_gh",
                side_effect=run_gh_side_effect,
                return_value=subprocess.CompletedProcess(["gh"], 0, "", ""),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log") as log,
        ):
            changed = github_bus.enable_review_pr_auto_merge(
                self._config(), bus_state, self.REPO, self._task()
            )
        entry = bus_state["tasks"][self.TASK_ID]
        return changed, entry, run_gh, log

    @staticmethod
    def _gh_calls(run_gh) -> list[list[str]]:
        return [call.args[0] for call in run_gh.call_args_list]

    def test_draft_pr_is_undrafted_then_armed(self) -> None:
        """GitHub refuses auto-merge when the publisher leaves an adopted PR as draft."""

        changed, entry, run_gh, log = self._arm(self._pr())

        self.assertTrue(changed)
        calls = self._gh_calls(run_gh)
        self.assertEqual(calls[0][:2], ["pr", "ready"])
        self.assertEqual(calls[1][:2], ["pr", "merge"])
        self.assertIn("--auto", calls[1])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_enabled")

    def test_ready_pr_is_armed_without_being_touched_first(self) -> None:
        changed, entry, run_gh, _ = self._arm(self._pr(isDraft=False))

        self.assertTrue(changed)
        calls = self._gh_calls(run_gh)
        self.assertEqual([call[:2] for call in calls], [["pr", "merge"]])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")

    def test_arming_is_not_repeated_once_github_reports_it(self) -> None:
        """The bus re-reads every approved PR each poll; a second call must be a no-op."""

        bus_state = self._bus_state()
        self._arm(self._pr(isDraft=False), bus_state=bus_state)
        changed, entry, run_gh, log = self._arm(
            self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-08-06T00:00:00Z"}),
            bus_state=bus_state,
        )

        self.assertFalse(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(log.call_count, 0)
        self.assertEqual(entry["auto_merge"]["state"], "enabled")

    def test_pr_against_the_default_branch_is_never_armed(self) -> None:
        """A stale ReviewBus PR aimed at main must not be handed the merge button."""

        changed, entry, run_gh, log = self._arm(self._pr(baseRefName="main"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_wrong_base")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_skipped")

    def test_pr_from_another_task_branch_is_never_armed(self) -> None:
        changed, entry, run_gh, _ = self._arm(self._pr(headRefName="task/ODP-OTHER-002"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_branch_mismatch")

    def test_conflicting_pr_is_left_for_a_rebase(self) -> None:
        changed, entry, run_gh, _ = self._arm(self._pr(mergeStateStatus="DIRTY"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_conflicting")

    def test_blocked_and_behind_prs_are_still_armed(self) -> None:
        """Waiting on checks or on a moved base is what auto-merge exists to absorb."""

        for merge_state in ("BLOCKED", "BEHIND", "UNSTABLE"):
            with self.subTest(merge_state=merge_state):
                changed, entry, run_gh, _ = self._arm(self._pr(isDraft=False, mergeStateStatus=merge_state))

                self.assertTrue(changed)
                self.assertEqual(entry["auto_merge"]["state"], "enabled")
                self.assertIn("--auto", self._gh_calls(run_gh)[0])

    def test_merged_pr_is_recorded_without_noise(self) -> None:
        changed, entry, run_gh, log = self._arm(self._pr(state="MERGED"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_pr_merged")
        self.assertEqual(log.call_count, 0)

    def test_missing_pr_number_is_skipped_quietly(self) -> None:
        _, entry, run_gh, log = self._arm(None, bus_state=self._bus_state(number=None))

        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_no_pr")
        self.assertEqual(log.call_count, 0)

    def test_gh_failure_is_recorded_once_not_every_poll(self) -> None:
        bus_state = self._bus_state()
        failure = github_bus.GitHubBusError("Auto-merge is not allowed for this repository")

        first_changed, entry, _, first_log = self._arm(
            self._pr(isDraft=False), bus_state=bus_state, run_gh_side_effect=failure
        )
        second_changed, entry, _, second_log = self._arm(
            self._pr(isDraft=False), bus_state=bus_state, run_gh_side_effect=failure
        )

        self.assertTrue(first_changed)
        self.assertEqual(first_log.call_args.args[1]["type"], "github_auto_merge_failed")
        self.assertFalse(second_changed)
        self.assertEqual(second_log.call_count, 0)
        self.assertEqual(entry["auto_merge"]["state"], "failed")

    def test_offline_propagates_for_bus_backoff(self) -> None:
        """Offline is the bus's own signal; swallowing it would hide the outage."""

        with self.assertRaises(github_bus.GitHubBusOffline):
            self._arm(
                self._pr(isDraft=False),
                run_gh_side_effect=github_bus.GitHubBusOffline("no such host"),
            )

    def _sync_and_capture_armed(self, tasks: list[dict], *, auto_merge: bool = True) -> list[str]:
        armed: list[str] = []
        config = self._config(auto_merge=auto_merge)
        config["github_bus"]["repo"] = self.REPO
        with (
            mock.patch.object(github_bus, "upsert_review_pr", return_value=False),
            mock.patch.object(github_bus, "upsert_ops_issue", return_value=False),
            mock.patch.object(
                github_bus,
                "enable_review_pr_auto_merge",
                side_effect=lambda c, b, r, t: armed.append(t["id"]) or False,
            ),
            mock.patch.object(
                github_bus,
                "sync_archive_housekeeping_auto_merge",
                return_value=False,
            ),
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            github_bus.sync_outbound(config, {"tasks": {}}, {"tasks": tasks, "blockers": []}, {}, self.REPO)
        return armed

    def test_sync_outbound_arms_approved_tasks_only(self) -> None:
        in_review = self._task("review")
        in_review["id"] = "ODP-STILL-IN-REVIEW-001"

        self.assertEqual(
            self._sync_and_capture_armed([self._task("review_approved"), in_review]),
            [self.TASK_ID],
        )

    def test_sync_outbound_respects_the_config_switch(self) -> None:
        self.assertEqual(self._sync_and_capture_armed([self._task()], auto_merge=False), [])

    def test_auto_merge_statuses_track_the_finalize_gate(self) -> None:
        config = {"ready_dispatcher": {"finalize_statuses": ["approved"]}}

        self.assertEqual(github_bus.auto_merge_statuses(config), {"approved"})
        self.assertEqual(github_bus.auto_merge_statuses({}), {"review_approved"})

    def test_config_switch_reads_branch_workflow_task_pr(self) -> None:
        self.assertTrue(github_bus.task_pr_auto_merge_enabled(self._config()))
        self.assertFalse(github_bus.task_pr_auto_merge_enabled(self._config(auto_merge=False)))
        self.assertFalse(github_bus.task_pr_auto_merge_enabled({}))
        self.assertFalse(
            github_bus.task_pr_auto_merge_enabled(
                {"branch_workflow": {"enabled": False, "task_pr": {"auto_merge": True}}}
            )
        )

    def test_archive_housekeeping_pr_uses_same_auto_merge_mutation(self) -> None:
        head = "task/OPS-ARCHIVE-AUTO-COMMIT-20260814T120000Z"
        url = f"https://github.com/{self.REPO}/pull/812"
        listed = {
            "number": 812,
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "dev",
            "headRefName": head,
            "url": url,
            "mergeStateStatus": "BLOCKED",
        }
        viewed = {**listed, "autoMergeRequest": None}

        def fake_gh_json(args: list[str]):
            return [listed] if args[:2] == ["pr", "list"] else viewed

        bus_state: dict = {}
        with (
            mock.patch.object(github_bus, "gh_json", side_effect=fake_gh_json),
            mock.patch.object(
                github_bus,
                "run_gh",
                return_value=subprocess.CompletedProcess(["gh"], 0, "", ""),
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.sync_archive_housekeeping_auto_merge(
                self._config(), bus_state, self.REPO
            )

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh)[0][:2], ["pr", "merge"])
        self.assertIn("--auto", self._gh_calls(run_gh)[0])
        entry = bus_state["housekeeping_prs"][head]
        self.assertEqual(entry["auto_merge"]["state"], "enabled")

    def test_archive_housekeeping_auto_merge_switch_fails_closed(self) -> None:
        config = self._config()
        config["github_bus"]["archive_housekeeping_auto_merge"] = False
        with mock.patch.object(github_bus, "gh_json") as gh_json:
            changed = github_bus.sync_archive_housekeeping_auto_merge(
                config, {}, self.REPO
            )

        self.assertFalse(changed)
        gh_json.assert_not_called()

    def test_cloud_relay_is_safe_off_by_default(self) -> None:
        config = {
            "paths": {"github_relay_state": "/tmp/oday-test-github-relay-state.json"},
            "github_bus": {"phase3": {"cloud_relay": {"url_env": "TEST_RELAY_URL"}}},
        }
        with mock.patch.object(github_cloud_relay, "relay_request") as request:
            self.assertEqual(github_cloud_relay.pull_commands(config), [])
            self.assertIsNone(github_cloud_relay.push_status_digest(config, {"ok": True}))
        request.assert_not_called()

    def test_cloud_relay_requires_explicit_enable(self) -> None:
        config = {
            "paths": {"github_relay_state": "/tmp/oday-test-github-relay-state.json"},
            "github_bus": {
                "phase3": {
                    "cloud_relay": {
                        "enabled": True,
                        "url_env": "TEST_RELAY_URL",
                        "token_env": "TEST_RELAY_TOKEN",
                    }
                }
            },
        }
        with (
            mock.patch.dict("os.environ", {"TEST_RELAY_URL": "https://relay.test", "TEST_RELAY_TOKEN": "token"}),
            mock.patch.object(github_cloud_relay, "relay_request", return_value={"commands": []}) as request,
            mock.patch.object(github_cloud_relay, "write_activity_log"),
        ):
            self.assertEqual(github_cloud_relay.pull_commands(config), [])
        request.assert_called_once()


class GhBinaryResolutionTests(unittest.TestCase):
    """The real CLI wins; `.orchestrator/bin/gh` is only a last resort.

    That file is a broker shim, not the real CLI
    (delivery_toolchain/git/README.md). task_finalize.sh,
    check_pr_merge_eligibility.py, apply_branch_protection.py and ai_status.py all
    discard a `gh` that resolves into `.orchestrator/bin/`; the bus was the one
    consumer that actively preferred it, which is how a shim that could not resolve
    its own target took the GitHub bus down while every other caller kept working.
    """

    def _vendored(self, root: Path) -> Path:
        vendored = root / ".orchestrator" / "bin" / "gh"
        vendored.parent.mkdir(parents=True, exist_ok=True)
        vendored.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        vendored.chmod(0o755)
        return vendored

    def test_system_gh_wins_even_when_the_shim_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._vendored(root)
            with (
                mock.patch.object(github_bus, "ROOT", root),
                mock.patch.object(common.shutil, "which", return_value="/usr/bin/gh"),
            ):
                self.assertEqual(github_bus.resolve_gh_binary(), "/usr/bin/gh")

    def test_shim_is_used_only_when_no_real_cli_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            vendored = self._vendored(root)
            with (
                mock.patch.object(github_bus, "ROOT", root),
                # The rule now lives in common.resolve_github_cli, so "no system
                # gh" is simulated at its seams, not at github_bus.command_exists.
                mock.patch.object(common.shutil, "which", return_value=None),
                mock.patch.object(common, "SYSTEM_GH_PATHS", ()),
            ):
                self.assertEqual(github_bus.resolve_gh_binary(), str(vendored))

    def test_returns_none_when_nothing_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                mock.patch.object(github_bus, "ROOT", Path(tmpdir)),
                mock.patch.object(common.shutil, "which", return_value=None),
                mock.patch.object(common, "SYSTEM_GH_PATHS", ()),
            ):
                self.assertIsNone(github_bus.resolve_gh_binary())


if __name__ == "__main__":
    unittest.main()
