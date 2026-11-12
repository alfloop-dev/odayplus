#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ai_status
import common
import dispatch_engine
import github_bus
import github_cloud_relay
import github_reconciliation
import status_transition
from check_task_delivery_identity import inspect_delivery_identity
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

    def _stale_ref_config(self) -> dict:
        return {
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            }
        }

    def _stale_ref_task(self, task_id: str, title: str) -> dict:
        return {
            "id": task_id,
            "title": title,
            "summary_zh": "reconcile me",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Codex",
            "depends_on": [],
            "artifacts": [],
            "next": "ready",
        }

    def test_stale_pr_number_is_not_carried_into_no_commits_skip(self) -> None:
        """A closed PR number must not survive the ``skipped_no_commits`` path.

        Regression: the stale null-out used to sit *after* the skip paths, so a
        poll that found no commits ahead of the base copied the dead PR number
        into ``state: skipped_no_commits`` -- a state that no longer looks
        stale.  The next poll (new head_sha, so the hash guard does not fire)
        then edited the CLOSED PR again and auto-merge stranded the task.
        """

        config = self._stale_ref_config()
        task = self._stale_ref_task("ODP-PR-STALE-SKIP-001", "Stale ref meets empty branch")
        branch = "task/ODP-PR-STALE-SKIP-001"
        bus_state = {
            "tasks": {
                task["id"]: {
                    "review_pr": {
                        "number": 1018,
                        "url": "https://example.test/pull/1018",
                        "branch": branch,
                        "state": "closed",
                    },
                    "last_review_hash": "stale-hash",
                }
            }
        }
        found = {"number": 1019, "url": "https://example.test/pull/1019"}

        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(github_bus, "remote_branch_head_sha", side_effect=["a" * 40, "c" * 40]),
            mock.patch.object(github_bus, "branch_has_diff", side_effect=[False, True]),
            mock.patch.object(github_bus, "find_existing_pr", return_value=found),
            mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
            mock.patch.object(github_bus, "edit_pull_request_rest") as edit,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            first = github_bus.upsert_review_pr(config, bus_state, {"tasks": []}, "o/r", task)
            skipped = bus_state["tasks"][task["id"]]["review_pr"]
            # The dead number must be dropped here, not merely ignored later.
            self.assertEqual(skipped["state"], "skipped_no_commits")
            self.assertIsNone(skipped["number"])
            self.assertIsNone(skipped["url"])

            second = github_bus.upsert_review_pr(config, bus_state, {"tasks": []}, "o/r", task)

        self.assertTrue(first)
        self.assertTrue(second)
        # The closed PR must never be edited; only the adopted open PR is.
        edited_numbers = [call.args[1] for call in edit.call_args_list]
        self.assertEqual(edited_numbers, [1019])
        review_pr = bus_state["tasks"][task["id"]]["review_pr"]
        self.assertEqual(review_pr["number"], 1019)
        self.assertEqual(review_pr["state"], "open")

    def test_stale_pr_number_is_not_carried_into_unpublished_skip(self) -> None:
        """Same carry-over hazard on the ``skipped_unpublished_branch`` path."""

        config = self._stale_ref_config()
        task = self._stale_ref_task("ODP-PR-STALE-SKIP-002", "Stale ref meets unpushed branch")
        branch = "task/ODP-PR-STALE-SKIP-002"
        bus_state = {
            "tasks": {
                task["id"]: {
                    "review_pr": {
                        "number": 1018,
                        "url": "https://example.test/pull/1018",
                        "branch": branch,
                        "state": "merged",
                    },
                    "last_review_hash": "stale-hash",
                }
            }
        }

        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value="a" * 40),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=None),
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, {"tasks": []}, "o/r", task)

        self.assertTrue(changed)
        review_pr = bus_state["tasks"][task["id"]]["review_pr"]
        self.assertEqual(review_pr["state"], "skipped_unpublished_branch")
        self.assertIsNone(review_pr["number"])
        self.assertIsNone(review_pr["url"])

    def test_github_cased_pr_states_are_treated_as_stale(self) -> None:
        """``poll_pr_reviews`` writes GitHub's casing into ``pr_ref["state"]``.

        ``pr view --json state`` returns ``OPEN``/``CLOSED``/``MERGED``, so a
        lowercase-only membership test would silently keep editing a dead PR.
        """

        for github_state in ("CLOSED", "MERGED"):
            with self.subTest(state=github_state):
                self.assertTrue(
                    github_bus.review_pr_ref_is_stale({"number": 1018, "state": github_state})
                )

                config = self._stale_ref_config()
                task = self._stale_ref_task(
                    "ODP-PR-STALE-CASE-001", "Uppercase stale state"
                )
                branch = "task/ODP-PR-STALE-CASE-001"
                bus_state = {
                    "tasks": {
                        task["id"]: {
                            "review_pr": {
                                "number": 1018,
                                "url": "https://example.test/pull/1018",
                                "branch": branch,
                                "state": github_state,
                            },
                            "last_review_hash": "stale-hash",
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
                    mock.patch.object(github_bus, "build_template_body", return_value="body\n"),
                    mock.patch.object(github_bus, "edit_pull_request_rest") as edit,
                    mock.patch.object(github_bus, "write_activity_log"),
                ):
                    changed = github_bus.upsert_review_pr(
                        config, bus_state, {"tasks": []}, "o/r", task
                    )

                self.assertTrue(changed)
                find.assert_called_once_with("o/r", task["id"], branch, "dev")
                self.assertEqual(edit.call_args.args[1], 1019)
                self.assertEqual(bus_state["tasks"][task["id"]]["review_pr"]["number"], 1019)

    def test_open_and_skipped_states_are_not_treated_as_stale(self) -> None:
        """Only dead-PR states are stale; live and transient ones are not."""

        for state in ("open", "OPEN", "skipped_no_commits", "skipped_unpublished_branch", "skipped_no_branch"):
            with self.subTest(state=state):
                self.assertFalse(github_bus.review_pr_ref_is_stale({"number": 7, "state": state}))
        self.assertFalse(github_bus.review_pr_ref_is_stale(None))
        self.assertFalse(github_bus.review_pr_ref_is_stale({}))

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

    def test_review_branch_for_task_rejects_substring_agent_branch(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {
            "agents": [
                {
                    "name": "Codex",
                    "branch": "task/ODP-FOO-0010",
                }
            ]
        }
        task = {
            "id": "ODP-FOO-001",
            "owner": "Codex",
            "title": "Parent task",
            "github": {"head_branch": "task/ODP-FOO-0010"},
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name in {
                "task/ODP-FOO-001",
                "task/ODP-FOO-0010",
            }

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(found_branch, "task/ODP-FOO-001")
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "origin/task/ODP-FOO-001"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-0010"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001X"))

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

    def test_task_id_matches_branch_accepts_canonical_and_suffixed_and_rejects_substrings(self) -> None:
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001-RECOVERY-20260911"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001-SIDECAR-ACCEPTANCE"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "ODP-FOO-001"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "ODP-FOO-001-RECOVERY"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP_FOO_001", "task/odp-foo-001-recovery"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "task/odp_foo_001_recovery"))
        self.assertTrue(github_bus.task_id_matches_branch("ODP-FOO-001", "origin/task/ODP-FOO-001"))

        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-0010"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-001X"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-0010-RECOVERY"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-FOO-002"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", "task/ODP-OTHER-001"))
        self.assertFalse(github_bus.task_id_matches_branch("", "task/ODP-FOO-001"))
        self.assertFalse(github_bus.task_id_matches_branch("ODP-FOO-001", ""))

    def test_review_branch_for_task_prefers_explicit_suffixed_branch_over_canonical_coexisting(self) -> None:
        config = {"branch_workflow": {"task_branch_prefix": "task/"}}
        status = {"agents": []}
        task = {
            "id": "ODP-GITHUB-GCP-ENV-BOOTSTRAP-001",
            "branch": "task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001-RECOVERY-20260911",
            "owner": "Antigravity",
            "title": "Bootstrap GCP env",
        }

        def mock_exists(branch_name: str) -> bool:
            return branch_name in {
                "task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001",
                "task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001-RECOVERY-20260911",
            }

        with mock.patch.object(github_bus, "branch_exists", side_effect=mock_exists):
            found_branch = github_bus.review_branch_for_task(config, status, task)

        self.assertEqual(found_branch, "task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001-RECOVERY-20260911")

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
    HEAD_SHA = "a" * 40

    def test_pr_publish_and_auto_merge_have_one_owner_each(self) -> None:
        root = Path(github_bus.__file__).resolve().parents[1]
        bus_source = Path(github_bus.__file__).read_text(encoding="utf-8")
        finalize_source = (root / "delivery_toolchain/git/task_finalize.sh").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('["pr", "create"', bus_source)
        # Every `gh pr merge` command - the queue/auto admission and the
        # dispatcher's direct merge on a base without a queue - is built in the
        # one helper that issues it and reads it back; nothing else in the bus
        # or the dispatcher issues one.
        mutation_owner = inspect.getsource(github_bus.issue_pr_merge_admission)
        self.assertEqual(bus_source.count('["pr", "merge"'), mutation_owner.count('["pr", "merge"'))
        self.assertEqual(mutation_owner.count("run_gh(command)"), 1)
        dispatch_source = (root / ".orchestrator/dispatch_engine.py").read_text(encoding="utf-8")
        self.assertNotIn('"pr", "merge"', dispatch_source)
        self.assertNotIn(" --auto", finalize_source)
        self.assertFalse((root / ".orchestrator/auto_merge_green_prs.py").exists())
        self.assertFalse((root / ".orchestrator/auto-merge-guard.sh").exists())

    def test_admission_readback_matches_branch_refs_exactly(self) -> None:
        """The readback identity is the PR as checked: the head SHA is hex and
        compares case-insensitively, but base and source branch are git refs
        and a case-only change names another ref."""

        checked = {
            "number": 77,
            "state": "OPEN",
            "baseRefName": "dev",
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD_SHA,
        }
        cases = {
            "same": ({}, True),
            "head sha case": ({"headRefOid": self.HEAD_SHA.upper()}, True),
            "base case": ({"baseRefName": "DEV"}, False),
            "branch case": ({"headRefName": "Task/ODP-ORCH-REVIEWBUS-AUTOMERGE-001"}, False),
            "branch lowered": ({"headRefName": self.BRANCH.lower()}, False),
            "other base": ({"baseRefName": "main"}, False),
            "other branch": ({"headRefName": "task/ODP-OTHER-001"}, False),
            "other head": ({"headRefOid": "b" * 40}, False),
            "missing base": ({"baseRefName": None}, False),
        }
        for name, (overrides, admitted) in cases.items():
            with self.subTest(name):
                answer = {**checked, **overrides}
                with mock.patch.object(github_bus, "gh_json", return_value=answer):
                    readback = github_bus.read_pr_merge_admission(self.REPO, 77, expected=checked)
                if admitted:
                    self.assertEqual(readback, answer)
                else:
                    self.assertIsNone(readback)

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
            "headRefOid": self.HEAD_SHA,
            "url": f"https://github.com/{self.REPO}/pull/700",
            "mergeStateStatus": "BLOCKED",
        }
        pr.update(overrides)
        return pr

    @staticmethod
    def _graphql_node(target: dict) -> dict:
        """The whole node `PR_MERGE_QUEUE_GRAPHQL_QUERY` asks GitHub for."""

        merged = str(target.get("state") or "").upper() == "MERGED"
        return {
            "number": target.get("number", 700),
            "state": target.get("state", "OPEN"),
            "merged": merged,
            "mergedAt": "2026-09-01T00:00:00Z" if merged else None,
            "baseRefName": target.get("baseRefName"),
            "headRefName": target.get("headRefName"),
            "headRefOid": target.get("headRefOid"),
            "isInMergeQueue": bool(target.get("isInMergeQueue")),
            "mergeQueueEntry": target.get("mergeQueueEntry"),
        }

    def _arm(
        self,
        pr: dict | None,
        *,
        bus_state: dict | None = None,
        run_gh_side_effect=None,
        readback: dict | None = None,
        graphql_before: dict | None = None,
        graphql_after: dict | None = None,
    ):
        """`graphql_before`/`graphql_after` override fields of the GraphQL node
        alone, before and after the first mutation, for a queue answer that
        no longer agrees with the PR view."""
        bus_state = bus_state if bus_state is not None else self._bus_state()
        view_calls = 0
        run_gh_calls = []

        def fake_run_gh(args, **kwargs):
            run_gh_calls.append(args)
            if run_gh_side_effect is not None:
                if isinstance(run_gh_side_effect, BaseException):
                    raise run_gh_side_effect
                if callable(run_gh_side_effect):
                    return run_gh_side_effect(args, **kwargs)
                return run_gh_side_effect
            return subprocess.CompletedProcess(["gh"], 0, "", "")

        def fake_gh_json(args, **kwargs):
            nonlocal view_calls
            if args[:2] == ["pr", "view"]:
                view_calls += 1
                cur = pr if view_calls == 1 else (readback if readback is not None else pr)
                if cur is None:
                    return None
                res = dict(cur)
                res.pop("isInMergeQueue", None)
                res.pop("mergeQueueEntry", None)
                return res
            if args[:2] == ["api", "graphql"]:
                target = readback if (run_gh_calls and readback is not None) else pr
                if target is None:
                    return None
                node = self._graphql_node(target)
                node.update((graphql_after if run_gh_calls else graphql_before) or {})
                return {"data": {"repository": {"pullRequest": node}}}
            return None

        with (
            mock.patch.object(
                github_bus,
                "gh_json",
                side_effect=fake_gh_json,
            ) as gh_json,
            mock.patch.object(
                github_bus,
                "run_gh",
                side_effect=fake_run_gh,
            ) as run_gh,
            mock.patch.object(github_bus, "write_activity_log") as log,
        ):
            changed = github_bus.enable_review_pr_auto_merge(
                self._config(), bus_state, self.REPO, self._task()
            )
        entry = bus_state["tasks"][self.TASK_ID]
        return changed, entry, run_gh, log, gh_json

    @staticmethod
    def _gh_calls(run_gh) -> list[list[str]]:
        return [call.args[0] for call in run_gh.call_args_list]

    def test_auto_merge_pr_fields_compatibility_with_gh_cli(self) -> None:
        """AUTO_MERGE_PR_FIELDS must contain only valid fields recognized by `gh pr view --json`."""
        declared_fields = [f.strip() for f in github_bus.AUTO_MERGE_PR_FIELDS.split(",") if f.strip()]

        # Verify that forbidden/non-CLI fields are never included in AUTO_MERGE_PR_FIELDS
        self.assertNotIn("isInMergeQueue", declared_fields)
        self.assertNotIn("mergeQueueEntry", declared_fields)

        # Expected safe schema
        expected_fields = {
            "number",
            "state",
            "isDraft",
            "autoMergeRequest",
            "baseRefName",
            "headRefName",
            "headRefOid",
            "url",
            "mergeStateStatus",
        }
        self.assertEqual(set(declared_fields), expected_fields)

        # If gh CLI is present on the system, verify against `gh pr view --help` JSON FIELDS
        gh_bin = github_bus.resolve_gh_binary()
        if gh_bin and Path(gh_bin).exists():
            proc = subprocess.run([gh_bin, "pr", "view", "--help"], capture_output=True, text=True, check=False)
            if proc.returncode == 0 and "JSON FIELDS" in proc.stdout:
                _, _, fields_section = proc.stdout.partition("JSON FIELDS\n")
                fields_text, _, _ = fields_section.partition("\n\n")
                available_fields = {
                    field.strip().rstrip(",")
                    for line in fields_text.splitlines()
                    for field in line.split()
                    if field.strip().rstrip(",")
                }
                for field in declared_fields:
                    self.assertIn(
                        field,
                        available_fields,
                        f"Field '{field}' in AUTO_MERGE_PR_FIELDS is not supported by gh pr view --json",
                    )
                self.assertNotIn("isInMergeQueue", available_fields)
                self.assertNotIn("mergeQueueEntry", available_fields)

    def test_merge_queue_graphql_query_and_fetcher_schema(self) -> None:
        """fetch_pr_merge_queue_status queries the expected GraphQL schema and parses responses."""
        self.assertIn("isInMergeQueue", github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)
        self.assertIn("mergeQueueEntry", github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)
        self.assertIn("position", github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)
        self.assertIn("state", github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)
        self.assertIn("enqueuedAt", github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)

        # Successful query parsing
        mock_payload = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "number": 1181,
                        "isInMergeQueue": True,
                        "mergeQueueEntry": {
                            "position": 1,
                            "state": "AWAITING_CHECKS",
                            "enqueuedAt": "2026-09-04T02:00:00Z",
                        },
                    }
                }
            }
        }
        with mock.patch.object(github_bus, "gh_json", return_value=mock_payload) as gh_json_mock:
            status = github_bus.fetch_pr_merge_queue_status("alfloop-dev/odayplus", 1181)
            self.assertEqual(
                status,
                {
                    "number": 1181,
                    "isInMergeQueue": True,
                    "mergeQueueEntry": {
                        "position": 1,
                        "state": "AWAITING_CHECKS",
                        "enqueuedAt": "2026-09-04T02:00:00Z",
                    },
                },
            )
            args = gh_json_mock.call_args[0][0]
            self.assertEqual(args[:2], ["api", "graphql"])
            self.assertIn("owner=alfloop-dev", args)
            self.assertIn("name=odayplus", args)
            self.assertIn("number=1181", args)

        # Invalid repo format returns None without executing gh_json
        with mock.patch.object(github_bus, "gh_json") as gh_json_mock:
            self.assertIsNone(github_bus.fetch_pr_merge_queue_status("invalid-repo", 1181))
            gh_json_mock.assert_not_called()

        # Malformed or error payload returns None
        with mock.patch.object(github_bus, "gh_json", return_value={"errors": [{"message": "Not found"}]}):
            self.assertIsNone(github_bus.fetch_pr_merge_queue_status("alfloop-dev/odayplus", 1181))

    def test_draft_pr_is_undrafted_then_armed(self) -> None:
        """GitHub refuses auto-merge when the publisher leaves an adopted PR as draft."""

        changed, entry, run_gh, log, gh_json = self._arm(
            self._pr(),
            readback=self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}),
        )

        self.assertTrue(changed)
        calls = self._gh_calls(run_gh)
        self.assertEqual(calls[0][:2], ["pr", "ready"])
        self.assertEqual(calls[1][:2], ["pr", "merge"])
        self.assertIn("--auto", calls[1])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_enabled")

    def test_ready_pr_is_armed_without_being_touched_first(self) -> None:
        changed, entry, run_gh, _, gh_json = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}),
        )

        self.assertTrue(changed)
        calls = self._gh_calls(run_gh)
        self.assertEqual([call[:2] for call in calls], [["pr", "merge"]])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")

    def test_arming_is_not_repeated_once_github_reports_it(self) -> None:
        """The bus re-reads every approved PR each poll; a second call must be a no-op."""

        bus_state = self._bus_state()
        self._arm(
            self._pr(isDraft=False),
            bus_state=bus_state,
            readback=self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}),
        )
        changed, entry, run_gh, log, _ = self._arm(
            self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-08-06T00:00:00Z"}),
            bus_state=bus_state,
        )

        self.assertFalse(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(log.call_count, 0)
        self.assertEqual(entry["auto_merge"]["state"], "enabled")

    def test_pr_against_the_default_branch_is_never_armed(self) -> None:
        """A stale ReviewBus PR aimed at main must not be handed the merge button."""

        changed, entry, run_gh, log, _ = self._arm(self._pr(baseRefName="main"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_wrong_base")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_skipped")

    def test_pr_from_another_task_branch_is_never_armed(self) -> None:
        changed, entry, run_gh, _, _ = self._arm(self._pr(headRefName="task/ODP-OTHER-002"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_branch_mismatch")

    def test_pr_from_suffixed_task_branch_is_armed(self) -> None:
        suffixed_head = f"{self.BRANCH}-RECOVERY-20260911"
        changed, entry, run_gh, _, _ = self._arm(
            self._pr(headRefName=suffixed_head, isDraft=False, mergeStateStatus="BLOCKED"),
            readback=self._pr(
                headRefName=suffixed_head,
                isDraft=False,
                mergeStateStatus="BLOCKED",
                autoMergeRequest={"enabledAt": "2026-09-11T00:00:00Z"},
            ),
        )

        self.assertTrue(changed)
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertIn("--auto", self._gh_calls(run_gh)[0])

    def test_conflicting_pr_is_left_for_a_rebase(self) -> None:
        changed, entry, run_gh, _, _ = self._arm(self._pr(mergeStateStatus="DIRTY"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_conflicting")

    def test_blocked_and_behind_prs_are_still_armed(self) -> None:
        """Waiting on checks or on a moved base is what auto-merge exists to absorb."""

        for merge_state in ("BLOCKED", "BEHIND", "UNSTABLE"):
            with self.subTest(merge_state=merge_state):
                changed, entry, run_gh, _, _ = self._arm(
                    self._pr(isDraft=False, mergeStateStatus=merge_state),
                    readback=self._pr(
                        isDraft=False,
                        mergeStateStatus=merge_state,
                        autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"},
                    ),
                )

                self.assertTrue(changed)
                self.assertEqual(entry["auto_merge"]["state"], "enabled")
                self.assertIn("--auto", self._gh_calls(run_gh)[0])

    def test_merged_pr_is_recorded_without_noise(self) -> None:
        changed, entry, run_gh, log, _ = self._arm(self._pr(state="MERGED"))

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_pr_merged")
        self.assertEqual(log.call_count, 0)

    def test_missing_pr_number_is_skipped_quietly(self) -> None:
        _, entry, run_gh, log, _ = self._arm(None, bus_state=self._bus_state(number=None))

        self.assertEqual(self._gh_calls(run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "skipped_no_pr")
        self.assertEqual(log.call_count, 0)

    def test_gh_failure_is_recorded_once_not_every_poll(self) -> None:
        bus_state = self._bus_state()
        failure = github_bus.GitHubBusError("Auto-merge is not allowed for this repository")

        first_changed, entry, _, first_log, _ = self._arm(
            self._pr(isDraft=False),
            bus_state=bus_state,
            run_gh_side_effect=failure,
        )
        second_changed, entry, _, second_log, _ = self._arm(
            self._pr(isDraft=False),
            bus_state=bus_state,
            run_gh_side_effect=failure,
        )

        self.assertTrue(first_changed)
        self.assertEqual(first_log.call_args.args[1]["type"], "github_auto_merge_failed")
        self.assertFalse(second_changed)
        self.assertEqual(second_log.call_count, 0)
        self.assertEqual(entry["auto_merge"]["state"], "failed")

    def test_queue_answer_for_another_base_or_branch_is_not_this_pr(self) -> None:
        """The GraphQL node is held to the PR view's base and source branch too.
        Before the mutation a mismatch is unknown and nothing is issued; after
        it, a queued answer for a retargeted PR is not this PR's admission."""
        queued = {"isInMergeQueue": True, "mergeQueueEntry": {"position": 1, "state": "QUEUED"}}
        for name, moved in {
            "another base": {"baseRefName": "main"},
            "base case": {"baseRefName": "DEV"},
            "another branch": {"headRefName": "task/ODP-OTHER-001"},
            "base missing": {"baseRefName": None},
        }.items():
            with self.subTest(name):
                _c, entry, run_gh, _l, _g = self._arm(self._pr(isDraft=False), graphql_before=moved)
                self.assertEqual(self._gh_calls(run_gh), [])
                self.assertEqual(entry["auto_merge"]["state"], "skipped_queue_unreadable")

                _c, entry, run_gh, _l, _g = self._arm(
                    self._pr(isDraft=False), graphql_after={**queued, **moved}
                )
                self.assertEqual([c[:2] for c in self._gh_calls(run_gh)], [["pr", "merge"]])
                self.assertEqual(entry["auto_merge"]["state"], "failed")
                self.assertEqual(entry["auto_merge"]["failure_code"], "auto_merge_unconfirmed_readback")

        # The same queued answer on the checked base and branch is the admission.
        _c, entry, _r, _l, _g = self._arm(self._pr(isDraft=False), graphql_after=queued)
        self.assertEqual(entry["auto_merge"]["state"], "queued")

    def test_a_failed_mutation_is_settled_by_its_readback(self) -> None:
        """A nonzero `gh pr merge` is not proof GitHub applied nothing. Its
        readback decides: armed is armed, a whole `not_admitted` is the
        refusal, and anything unsettled is an unconfirmed retry - never the
        error recorded as if nothing happened, never an admission."""
        bad_gateway = github_bus.GitHubBusError("HTTP 502: Bad Gateway (https://api.github.com/graphql)")

        def merge_fails(args, **_kwargs):
            if args[:2] == ["pr", "merge"]:
                raise bad_gateway
            return subprocess.CompletedProcess(["gh"], 0, "", "")

        _c, entry, _r, log, _g = self._arm(
            self._pr(isDraft=False),
            run_gh_side_effect=merge_fails,
            readback=self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}),
        )
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_enabled")

        _c, entry, _r, _l, _g = self._arm(
            self._pr(isDraft=False), run_gh_side_effect=merge_fails, graphql_after={"state": None}
        )
        self.assertEqual(entry["auto_merge"]["state"], "failed")
        self.assertEqual(entry["auto_merge"]["failure_code"], "auto_merge_unconfirmed_readback")

        _c, entry, _r, _l, _g = self._arm(self._pr(isDraft=False), run_gh_side_effect=merge_fails)
        self.assertEqual(entry["auto_merge"]["state"], "failed")
        self.assertNotIn("failure_code", entry["auto_merge"])
        self.assertIn("502", entry["auto_merge"]["message"])

    def test_zero_exit_without_armed_readback_fails_closed_and_retries(self) -> None:
        bus_state = self._bus_state()

        first_changed, entry, first_run_gh, first_log, first_gh_json = self._arm(
            self._pr(isDraft=False),
            bus_state=bus_state,
            readback=self._pr(isDraft=False),
        )

        self.assertTrue(first_changed)
        self.assertEqual([call[:2] for call in self._gh_calls(first_run_gh)], [["pr", "merge"]])
        expected_view = [
            "pr",
            "view",
            "700",
            "--repo",
            self.REPO,
            "--json",
            github_bus.AUTO_MERGE_PR_FIELDS,
        ]
        view_calls = [c.args[0] for c in first_gh_json.call_args_list if c.args[0][:2] == ["pr", "view"]]
        self.assertEqual(view_calls, [expected_view, expected_view])
        graphql_calls = [c.args[0] for c in first_gh_json.call_args_list if c.args[0][:2] == ["api", "graphql"]]
        self.assertEqual(len(graphql_calls), 2)
        self.assertEqual(entry["auto_merge"]["state"], "failed")
        self.assertTrue(entry["auto_merge"]["retryable"])
        self.assertEqual(entry["auto_merge"]["failure_code"], "auto_merge_unconfirmed_readback")
        self.assertEqual(first_log.call_args.args[1]["type"], "github_auto_merge_failed")

        second_changed, entry, second_run_gh, second_log, second_gh_json = self._arm(
            self._pr(isDraft=False),
            bus_state=bus_state,
            readback=self._pr(
                isDraft=False,
                autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"},
            ),
        )

        self.assertTrue(second_changed)
        self.assertEqual([call[:2] for call in self._gh_calls(second_run_gh)], [["pr", "merge"]])
        second_view_calls = [c.args[0] for c in second_gh_json.call_args_list if c.args[0][:2] == ["pr", "view"]]
        self.assertEqual(second_view_calls, [expected_view, expected_view])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertEqual(second_log.call_args.args[1]["type"], "github_auto_merge_enabled")

    def test_successful_mutation_readback_accepts_merged_pr(self) -> None:
        changed, entry, run_gh, log, gh_json = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(isDraft=False, state="MERGED", autoMergeRequest=None),
        )

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh)[0][:2], ["pr", "merge"])
        self.assertEqual(entry["auto_merge"]["state"], "enabled")
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_enabled")

    def test_merge_queue_readback_accepts_queued_pr_awaiting_checks(self) -> None:
        changed, entry, run_gh, log, gh_json = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(
                isDraft=False,
                autoMergeRequest=None,
                isInMergeQueue=True,
                mergeQueueEntry={
                    "position": 1,
                    "state": "AWAITING_CHECKS",
                    "enqueuedAt": "2026-09-04T02:00:00Z",
                },
            ),
        )

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh)[0][:2], ["pr", "merge"])
        self.assertEqual(entry["auto_merge"]["state"], "queued")
        self.assertTrue(entry["auto_merge"]["is_in_merge_queue"])
        self.assertEqual(entry["auto_merge"]["queue_state"], "AWAITING_CHECKS")
        self.assertEqual(entry["auto_merge"]["queue_position"], 1)
        self.assertEqual(
            entry["auto_merge"]["message"],
            "PR #700 is in the merge queue for `dev` at position 1 (AWAITING_CHECKS).",
        )
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_queued")
        self.assertEqual(log.call_args.args[1]["queue_state"], "AWAITING_CHECKS")
        self.assertEqual(log.call_args.args[1]["queue_position"], 1)

    def test_merge_queue_readback_accepts_unmergeable_queue_state(self) -> None:
        changed, entry, run_gh, log, gh_json = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(
                isDraft=False,
                autoMergeRequest=None,
                isInMergeQueue=True,
                mergeQueueEntry={
                    "position": 3,
                    "state": "UNMERGEABLE",
                },
            ),
        )

        self.assertTrue(changed)
        self.assertEqual(self._gh_calls(run_gh)[0][:2], ["pr", "merge"])
        self.assertEqual(entry["auto_merge"]["state"], "queued")
        self.assertEqual(entry["auto_merge"]["queue_state"], "UNMERGEABLE")
        self.assertEqual(entry["auto_merge"]["queue_position"], 3)
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_queued")
        self.assertEqual(log.call_args.args[1]["queue_state"], "UNMERGEABLE")
        self.assertEqual(log.call_args.args[1]["queue_position"], 3)

    def test_pr_already_in_merge_queue_skips_gh_merge_call(self) -> None:
        bus_state = self._bus_state()
        queued_pr = self._pr(
            isDraft=False,
            autoMergeRequest=None,
            isInMergeQueue=True,
            mergeQueueEntry={
                "position": 1,
                "state": "AWAITING_CHECKS",
            },
        )
        first_changed, entry, first_run_gh, first_log, first_gh_json = self._arm(
            queued_pr,
            bus_state=bus_state,
        )

        self.assertTrue(first_changed)
        self.assertEqual(self._gh_calls(first_run_gh), [])
        self.assertEqual(entry["auto_merge"]["state"], "queued")
        self.assertEqual(first_log.call_args.args[1]["type"], "github_auto_merge_queued")

        # Second poll with unchanged merge queue status is quiet and makes no gh merge calls
        second_changed, entry, second_run_gh, second_log, second_gh_json = self._arm(
            queued_pr,
            bus_state=bus_state,
        )
        self.assertFalse(second_changed)
        self.assertEqual(self._gh_calls(second_run_gh), [])
        self.assertEqual(second_log.call_count, 0)

    def test_merge_queue_movement_and_state_transition_are_audited(self) -> None:
        bus_state = self._bus_state()
        # Initial poll: position 2, AWAITING_CHECKS
        self._arm(
            self._pr(
                isDraft=False,
                isInMergeQueue=True,
                mergeQueueEntry={"position": 2, "state": "AWAITING_CHECKS"},
            ),
            bus_state=bus_state,
        )
        self.assertEqual(bus_state["tasks"][self.TASK_ID]["auto_merge"]["queue_position"], 2)

        # Movement: advances to position 1
        changed, entry, _, log, _ = self._arm(
            self._pr(
                isDraft=False,
                isInMergeQueue=True,
                mergeQueueEntry={"position": 1, "state": "AWAITING_CHECKS"},
            ),
            bus_state=bus_state,
        )
        self.assertTrue(changed)
        self.assertEqual(entry["auto_merge"]["queue_position"], 1)
        self.assertEqual(log.call_args.args[1]["queue_position"], 1)

        # State transition: becomes MERGEABLE
        changed, entry, _, log, _ = self._arm(
            self._pr(
                isDraft=False,
                isInMergeQueue=True,
                mergeQueueEntry={"position": 1, "state": "MERGEABLE"},
            ),
            bus_state=bus_state,
        )
        self.assertTrue(changed)
        self.assertEqual(entry["auto_merge"]["queue_state"], "MERGEABLE")
        self.assertEqual(log.call_args.args[1]["queue_state"], "MERGEABLE")

    def test_merge_queue_without_entry_detail_records_cleanly(self) -> None:
        changed, entry, run_gh, log, _ = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(
                isDraft=False,
                autoMergeRequest=None,
                isInMergeQueue=True,
                mergeQueueEntry=None,
            ),
        )

        self.assertTrue(changed)
        self.assertEqual(entry["auto_merge"]["state"], "queued")
        self.assertTrue(entry["auto_merge"]["is_in_merge_queue"])
        self.assertIsNone(entry["auto_merge"]["queue_state"])
        self.assertIsNone(entry["auto_merge"]["queue_position"])
        self.assertEqual(
            entry["auto_merge"]["message"],
            "PR #700 is in the merge queue for `dev`.",
        )
        self.assertEqual(log.call_args.args[1]["type"], "github_auto_merge_queued")

    def test_contradictory_queued_node_is_not_queued_before_or_after_arming(self) -> None:
        """A queue flag counts only on a node that is OPEN and unmerged.
        Before arming a contradictory node is unknown and nothing is issued;
        after arming it does not confirm the mutation."""
        contradictions = {
            "CLOSED, merged false": {"state": "CLOSED"},
            "state null, merged false": {"state": None},
            "OPEN, merged null": {"merged": None},
        }
        for name, contradiction in contradictions.items():
            for entry in (None, {"position": 1, "state": "AWAITING_CHECKS"}):
                node = {**contradiction, "isInMergeQueue": True, "mergeQueueEntry": entry}
                with self.subTest(node=name, entry=entry, read="before"):
                    _c, record, run_gh, _l, _g = self._arm(self._pr(isDraft=False), graphql_before=node)
                    self.assertEqual(self._gh_calls(run_gh), [])
                    self.assertEqual(record["auto_merge"]["state"], "skipped_queue_unreadable")

                with self.subTest(node=name, entry=entry, read="after"):
                    _c, record, run_gh, _l, _g = self._arm(self._pr(isDraft=False), graphql_after=node)
                    self.assertEqual([c[:2] for c in self._gh_calls(run_gh)], [["pr", "merge"]])
                    self.assertEqual(record["auto_merge"]["state"], "failed")
                    self.assertEqual(record["auto_merge"]["failure_code"], "auto_merge_unconfirmed_readback")

    def test_auto_merge_request_on_a_closed_readback_is_not_enabled(self) -> None:
        """Same PR, same identity, CLOSED, still carrying `autoMergeRequest`:
        not armed, whether `gh pr merge` exited zero or failed. A failed
        mutation is not reported as refused either - the readback is not a
        whole statement that GitHub holds nothing."""
        closed_armed = self._pr(
            isDraft=False, state="CLOSED", autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}
        )
        for name, side_effect in {
            "zero exit": None,
            "failed exit": github_bus.GitHubBusError("HTTP 502: Bad Gateway"),
        }.items():
            with self.subTest(name):
                _c, record, run_gh, _l, gh_json = self._arm(
                    self._pr(isDraft=False), readback=closed_armed, run_gh_side_effect=side_effect
                )
                self.assertEqual([c[:2] for c in self._gh_calls(run_gh)], [["pr", "merge"]])
                self.assertEqual(record["auto_merge"]["state"], "failed")
                self.assertEqual(record["auto_merge"]["failure_code"], "auto_merge_unconfirmed_readback")

        # The OPEN PR carrying the same request is armed.
        _c, record, _r, _l, _g = self._arm(
            self._pr(isDraft=False),
            readback=self._pr(isDraft=False, autoMergeRequest={"enabledAt": "2026-09-01T00:00:00Z"}),
        )
        self.assertEqual(record["auto_merge"]["state"], "enabled")

    def test_offline_propagates_for_bus_backoff(self) -> None:
        """Offline is the bus's own signal; swallowing it would hide the outage."""

        with self.assertRaises(github_bus.GitHubBusOffline):
            self._arm(
                self._pr(isDraft=False),
                run_gh_side_effect=github_bus.GitHubBusOffline("no such host"),
            )

    def _sync_and_capture_armed(
        self, tasks: list[dict], *, auto_merge: bool = True, bus_state: dict | None = None
    ) -> list[str]:
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
            mock.patch.object(github_bus, "run_gh") as run_gh,
            mock.patch.object(github_bus, "gh_json") as gh_json,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            github_bus.sync_outbound(
                config,
                bus_state if bus_state is not None else {"tasks": {}},
                {"tasks": tasks, "blockers": []},
                {},
                self.REPO,
            )
        # Projection is read-only toward GitHub: not even a readback.
        run_gh.assert_not_called()
        gh_json.assert_not_called()
        return armed

    def test_sync_outbound_never_arms_canonical_tasks(self) -> None:
        """Admission of an approved task belongs to the dispatcher route, which
        keeps the exact-head window and attempt cap under canonical CAS. A bus
        mutation beside it is a second owner with no counter."""
        in_review = self._task("review")
        in_review["id"] = "ODP-STILL-IN-REVIEW-001"

        for auto_merge in (True, False):
            with self.subTest(auto_merge=auto_merge):
                self.assertEqual(
                    self._sync_and_capture_armed(
                        [self._task("review_approved"), in_review], auto_merge=auto_merge
                    ),
                    [],
                )

    def test_sync_outbound_projects_the_dispatcher_record_for_approved_tasks(self) -> None:
        approved = self._task("review_approved")
        approved.update(
            {
                "pr_number": 700,
                "approved_head": "a" * 40,
                "merge_route": {
                    "head": "a" * 40,
                    "route": "queued",
                    "pr_number": 700,
                    "at": "2026-11-11T00:00:00Z",
                    "attempts": 2,
                },
            }
        )
        in_review = self._task("review")
        in_review["id"] = "ODP-STILL-IN-REVIEW-001"
        bus_state = self._bus_state()

        self._sync_and_capture_armed([approved, in_review], bus_state=bus_state)

        projected = bus_state["tasks"][self.TASK_ID]["auto_merge"]
        self.assertEqual(projected["state"], "queued")
        self.assertEqual(projected["attempts"], 2)
        self.assertEqual(projected["head"], "a" * 40)
        self.assertEqual(projected["owner"], "dispatch_engine.route_approved_pr_to_merge")
        self.assertNotIn("ODP-STILL-IN-REVIEW-001", bus_state["tasks"])

        # Unchanged record: no rewrite, so no churn in bus state.
        before = dict(projected)
        self.assertFalse(github_bus.project_task_merge_admission(bus_state, approved))
        self.assertEqual(bus_state["tasks"][self.TASK_ID]["auto_merge"], before)

    def test_an_unrouted_approved_task_is_projected_as_awaiting_the_dispatcher(self) -> None:
        bus_state = self._bus_state()
        approved = self._task("review_approved")

        self.assertTrue(github_bus.project_task_merge_admission(bus_state, approved))
        self.assertEqual(
            bus_state["tasks"][self.TASK_ID]["auto_merge"]["state"], "awaiting_dispatcher"
        )

    def test_unknown_queue_enrollment_is_not_rearmed(self) -> None:
        """None from GraphQL is unknown, not "not queued"."""

        def run_gh_side_effect(args, **kwargs):
            return subprocess.CompletedProcess(["gh"], 0, "", "")

        pr = self._pr(isDraft=False)
        bus_state = self._bus_state()
        with (
            mock.patch.object(
                github_bus,
                "gh_json",
                side_effect=lambda args, **_k: dict(pr) if args[:2] == ["pr", "view"] else None,
            ),
            mock.patch.object(github_bus, "run_gh", side_effect=run_gh_side_effect) as run_gh,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            github_bus.enable_review_pr_auto_merge(self._config(), bus_state, self.REPO, self._task())

        run_gh.assert_not_called()
        self.assertEqual(
            bus_state["tasks"][self.TASK_ID]["auto_merge"]["state"], "skipped_queue_unreadable"
        )

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
            "headRefOid": "b" * 40,
            "url": url,
            "mergeStateStatus": "BLOCKED",
        }
        viewed = {**listed, "autoMergeRequest": None}
        armed = {**viewed, "autoMergeRequest": {"enabledAt": "2026-09-01T00:00:00Z"}}
        view_readbacks = iter([viewed, armed])

        def fake_gh_json(args: list[str]):
            if args[:2] == ["pr", "list"]:
                return [listed]
            if args[:2] == ["pr", "view"]:
                return next(view_readbacks)
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": self._graphql_node(listed)}}}
            return None

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

    def _housekeeping_round(self, viewed: list[dict], node) -> tuple[list[list[str]], dict]:
        """One real `sync_archive_housekeeping_auto_merge` pass: `viewed` are
        the successive `gh pr view` answers, `node(listed)` the GraphQL node."""

        head = "task/OPS-ARCHIVE-AUTO-COMMIT-20260814T120000Z"
        listed = dict(viewed[0])
        view_readbacks = iter(viewed)

        def fake_gh_json(args: list[str]):
            if args[:2] == ["pr", "list"]:
                return [listed]
            if args[:2] == ["pr", "view"]:
                return next(view_readbacks)
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": node(listed)}}}
            return None

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
            github_bus.sync_archive_housekeeping_auto_merge(self._config(), bus_state, self.REPO)
        return self._gh_calls(run_gh), bus_state["housekeeping_prs"][head]["auto_merge"]

    def _housekeeping_pr(self, **overrides) -> dict:
        pr = {
            "number": 812,
            "state": "OPEN",
            "isDraft": False,
            "autoMergeRequest": None,
            "baseRefName": "dev",
            "headRefName": "task/OPS-ARCHIVE-AUTO-COMMIT-20260814T120000Z",
            "headRefOid": "b" * 40,
            "url": f"https://github.com/{self.REPO}/pull/812",
            "mergeStateStatus": "BLOCKED",
        }
        pr.update(overrides)
        return pr

    def test_archive_housekeeping_partial_queue_answer_arms_nothing(self) -> None:
        """Housekeeping shares the reader: a partial or mismatched node is
        unknown there too, and unknown never re-issues the mutation."""

        broken = {
            "flag missing": lambda n: {k: v for k, v in n.items() if k != "isInMergeQueue"},
            "entry missing": lambda n: {k: v for k, v in n.items() if k != "mergeQueueEntry"},
            "merged missing": lambda n: {k: v for k, v in n.items() if k != "merged"},
            "entry beside a false flag": lambda n: {**n, "mergeQueueEntry": {"position": 1}},
            "another head": lambda n: {**n, "headRefOid": "c" * 40},
            "another PR": lambda n: {**n, "number": 813},
        }
        for name, mangle in broken.items():
            with self.subTest(name):
                calls, record = self._housekeeping_round(
                    [self._housekeeping_pr()], lambda listed, mangle=mangle: mangle(self._graphql_node(listed))
                )
                self.assertEqual(calls, [])
                self.assertEqual(record["state"], "skipped_queue_unreadable")

    # `gh pr view` answers that do not say whether the PR is armed: the field
    # it was asked for is missing, or not GitHub's nullable object.
    UNREPORTED_ARMING = {
        "field missing": "<drop>",
        "true": True,
        "list": [{"enabledAt": "2026-09-01T00:00:00Z"}],
        "string": "",
    }

    def _housekeeping_view(self, arming) -> dict:
        pr = self._housekeeping_pr()
        if arming == "<drop>":
            pr.pop("autoMergeRequest")
        else:
            pr["autoMergeRequest"] = arming
        return pr

    def test_archive_housekeeping_unreported_arming_is_not_absence(self) -> None:
        """The queue node does not carry arming state, so a whole not-queued
        node beside a view that did not report arming is still unknown:
        nothing is issued on it, and a readback like it after the mutation
        is not counted as armed, queued or refused."""

        for name, arming in self.UNREPORTED_ARMING.items():
            with self.subTest(name, read="before the mutation"):
                calls, record = self._housekeeping_round(
                    [self._housekeeping_view(arming)], self._graphql_node
                )
                self.assertEqual(calls, [])
                self.assertEqual(record["state"], "skipped_queue_unreadable")

            with self.subTest(name, read="after the mutation"):
                calls, record = self._housekeeping_round(
                    [self._housekeeping_pr(), self._housekeeping_view(arming)], self._graphql_node
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][:2], ["pr", "merge"])
                self.assertEqual(record["state"], "failed")
                self.assertEqual(record["failure_code"], "auto_merge_unconfirmed_readback")

        # Controls: an explicit null is absence and is admitted once; GitHub's
        # object is armed and nothing is issued.
        calls, record = self._housekeeping_round(
            [self._housekeeping_pr(), self._housekeeping_view({"enabledAt": "2026-09-01T00:00:00Z"})],
            self._graphql_node,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(record["state"], "enabled")
        calls, record = self._housekeeping_round(
            [self._housekeeping_view({"enabledAt": "2026-09-01T00:00:00Z"})], self._graphql_node
        )
        self.assertEqual(calls, [])
        self.assertEqual(record["state"], "enabled")

    def test_archive_housekeeping_readback_on_a_moved_pr_is_not_enabled(self) -> None:
        armed = {"autoMergeRequest": {"enabledAt": "2026-09-01T00:00:00Z"}}
        for name, moved in {
            "head": {"headRefOid": "c" * 40},
            "base": {"baseRefName": "main"},
            "branch": {"headRefName": "task/OPS-ARCHIVE-AUTO-COMMIT-20260815T000000Z"},
            "base case": {"baseRefName": "DEV"},
            "branch case": {"headRefName": "task/ops-archive-auto-commit-20260814t120000z"},
        }.items():
            with self.subTest(name):
                calls, record = self._housekeeping_round(
                    [self._housekeeping_pr(), self._housekeeping_pr(**armed, **moved)],
                    self._graphql_node,
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][:2], ["pr", "merge"])
                self.assertEqual(record["state"], "failed")
                self.assertEqual(record["failure_code"], "auto_merge_unconfirmed_readback")

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


class ReconcilePrBodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = (
            f"{github_bus.COMMENT_MARKER}\n"
            "# Pantheon Review Bus\n\n"
            "## 任務資訊\n"
            "- 任務 ID: `OPS-REVIEW-PR-BODY-PRESERVE-001`\n"
            "- 標題: 保留人工整理的繁中 PR body，避免 Review Bus 狀態同步覆寫\n"
            "- 狀態: `review`\n"
            "- 負責人: `Antigravity3`\n"
            "- 評審人: `Codex2`\n\n"
            "## 審查範圍\n"
            "- `.orchestrator/github_bus.py`\n\n"
            "## 分支資訊\n"
            "- 來源分支: `task/OPS-REVIEW-PR-BODY-PRESERVE-001`\n"
            "- 目標分支: `dev`\n\n"
            "## 下一步\n"
            "ready for review\n\n"
            "## 行動審查指南\n"
            "請使用 GitHub Mobile PR 審查動作：\n"
            "- `Approve`\n\n"
            "Orchestrator 會輪詢審查結果並自動同步回寫至 `ai-status.json`。\n"
            f"{github_bus.BUS_END_MARKER}\n"
        )

    def test_reconcile_empty_or_none_uses_template(self) -> None:
        self.assertEqual(github_bus.reconcile_pr_body(None, self.template), self.template.strip() + "\n")
        self.assertEqual(github_bus.reconcile_pr_body("", self.template), self.template.strip() + "\n")
        self.assertEqual(github_bus.reconcile_pr_body("   \n\t  ", self.template), self.template.strip() + "\n")

    def test_preserves_custom_human_body_without_markers(self) -> None:
        human_body = (
            "# 繁中 PR 說明\n"
            "這是一份由開發者人工整理的繁體中文 PR 說明。\n\n"
            "## 改動細節\n"
            "- 項目 1: 修正 Review Bus 覆寫問題\n"
            "- 項目 2: 支援 PR body 保留\n"
        )
        reconciled = github_bus.reconcile_pr_body(human_body, self.template)
        self.assertTrue(reconciled.startswith(human_body.strip()))
        self.assertIn("---", reconciled)
        self.assertIn(github_bus.COMMENT_MARKER, reconciled)
        self.assertIn(github_bus.BUS_END_MARKER, reconciled)

    def test_reconcile_is_strictly_idempotent(self) -> None:
        human_body = (
            "# 繁中 PR 說明\n"
            "人工撰寫內容\n"
        )
        first_pass = github_bus.reconcile_pr_body(human_body, self.template)
        second_pass = github_bus.reconcile_pr_body(first_pass, self.template)
        third_pass = github_bus.reconcile_pr_body(second_pass, self.template)
        self.assertEqual(first_pass, second_pass)
        self.assertEqual(second_pass, third_pass)

    def test_updates_bus_block_while_preserving_prefix_content(self) -> None:
        prefix = (
            "# 繁中 PR 說明\n"
            "人工撰寫內容\n\n"
            "---\n"
        )
        old_body = prefix + self.template
        updated_template = self.template.replace("狀態: `review`", "狀態: `review_approved`")
        reconciled = github_bus.reconcile_pr_body(old_body, updated_template)
        self.assertTrue(reconciled.startswith("# 繁中 PR 說明\n人工撰寫內容"))
        self.assertIn("狀態: `review_approved`", reconciled)
        self.assertNotIn("狀態: `review`\n", reconciled)

    def test_preserves_suffix_content_below_bus_block(self) -> None:
        suffix = "\n\n---\n## 補充說明\n下方人工整理筆記\n"
        old_body = self.template + suffix
        updated_template = self.template.replace("狀態: `review`", "狀態: `done`")
        reconciled = github_bus.reconcile_pr_body(old_body, updated_template)
        self.assertIn("狀態: `done`", reconciled)
        self.assertTrue(reconciled.endswith("## 補充說明\n下方人工整理筆記\n"))

    def test_preserves_both_prefix_and_suffix_content(self) -> None:
        prefix = "## 上方筆記\n前置說明\n\n---\n"
        suffix = "\n\n---\n## 下方筆記\n後置說明\n"
        old_body = prefix + self.template + suffix
        updated_template = self.template.replace("狀態: `review`", "狀態: `review_approved`")
        reconciled = github_bus.reconcile_pr_body(old_body, updated_template)
        self.assertTrue(reconciled.startswith("## 上方筆記\n前置說明"))
        self.assertIn("狀態: `review_approved`", reconciled)
        self.assertTrue(reconciled.endswith("## 下方筆記\n後置說明\n"))

    def test_handles_legacy_bus_template_without_end_marker(self) -> None:
        legacy_template = (
            f"{github_bus.COMMENT_MARKER}\n"
            "# Pantheon Review Bus\n\n"
            "## Task\n"
            "- ID: `LIN-001`\n\n"
            "## Mobile Review Guidance\n"
            "The orchestrator polls review results and writes them back into `ai-status.json`.\n"
        )
        custom_suffix = "\n\n## 人工自訂備註\n這是舊範本下方新增的自訂內容\n"
        old_body = legacy_template + custom_suffix
        reconciled = github_bus.reconcile_pr_body(old_body, self.template)
        self.assertIn(github_bus.BUS_END_MARKER, reconciled)
        self.assertIn("## 人工自訂備註\n這是舊範本下方新增的自訂內容", reconciled)

    def test_handles_chinese_legacy_bus_template_without_end_marker(self) -> None:
        legacy_chinese_template = (
            f"{github_bus.COMMENT_MARKER}\n"
            "# Pantheon Review Bus\n\n"
            "## 任務資訊\n"
            "- 任務 ID: `LIN-002`\n\n"
            "## 行動審查指南\n"
            "Orchestrator 會輪詢審查結果並自動同步回寫至 `ai-status.json`。\n"
        )
        custom_suffix = "\n\n## 人工自訂備註\n這是中文舊範本下方新增的自訂內容\n"
        old_body = legacy_chinese_template + custom_suffix
        reconciled = github_bus.reconcile_pr_body(old_body, self.template)
        self.assertIn(github_bus.BUS_END_MARKER, reconciled)
        self.assertIn("## 人工自訂備註\n這是中文舊範本下方新增的自訂內容", reconciled)

    def test_unrecognized_unclosed_block_fails_closed_and_preserves_body(self) -> None:
        unknown_legacy_body = (
            f"{github_bus.COMMENT_MARKER}\n"
            "# 未知格式的 PR 內容\n"
            "這裡包含人工整理的重要分析與排查紀錄，但沒有標準結尾 marker 也沒有已知舊終止句。\n\n"
            "## 關鍵變更\n"
            "- 項目 A\n"
            "- 項目 B\n"
        )
        # First reconcile pass
        reconciled = github_bus.reconcile_pr_body(unknown_legacy_body, self.template)
        self.assertEqual(reconciled, unknown_legacy_body.strip() + "\n")
        self.assertIn("這裡包含人工整理的重要分析與排查紀錄", reconciled)
        self.assertIn("- 項目 A", reconciled)

        # Subsequent passes must be strictly idempotent
        second_pass = github_bus.reconcile_pr_body(reconciled, self.template)
        third_pass = github_bus.reconcile_pr_body(second_pass, self.template)
        self.assertEqual(reconciled, second_pass)
        self.assertEqual(second_pass, third_pass)

    def test_unrecognized_unclosed_block_with_prefix_fails_closed(self) -> None:
        prefix_unknown_body = (
            "# 開發前置說明\n"
            "前置人工備註內容\n\n"
            f"{github_bus.COMMENT_MARKER}\n"
            "未閉合且無已知終止句的自訂內容區塊\n"
        )
        reconciled = github_bus.reconcile_pr_body(prefix_unknown_body, self.template)
        self.assertEqual(reconciled, prefix_unknown_body.strip() + "\n")
        self.assertIn("前置人工備註內容", reconciled)
        self.assertIn("未閉合且無已知終止句的自訂內容區塊", reconciled)

        second_pass = github_bus.reconcile_pr_body(reconciled, self.template)
        self.assertEqual(reconciled, second_pass)

    def test_chinese_review_template_rendering(self) -> None:
        config = {
            "github_bus": {
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"}
            }
        }
        variables = {
            "marker": github_bus.COMMENT_MARKER,
            "task_id": "OPS-REVIEW-PR-BODY-PRESERVE-001",
            "task_title": "保留人工整理的繁中 PR body",
            "task_summary": "修正 Review Bus 覆寫問題",
            "task_status": "review",
            "task_owner": "Antigravity3",
            "task_reviewer": "Codex2",
            "depends_on": "OPS-AGY-HARD-TIMEOUT-2H-001",
            "next_step": "ready for review",
            "artifacts": "- `.orchestrator/github_bus.py`",
            "branch": "task/OPS-REVIEW-PR-BODY-PRESERVE-001",
            "base_branch": "dev",
        }
        rendered = github_bus.build_template_body(config, "review_pr", variables)
        self.assertTrue(rendered.startswith(github_bus.COMMENT_MARKER))
        self.assertTrue(rendered.strip().endswith(github_bus.BUS_END_MARKER))
        self.assertIn("## 任務資訊", rendered)
        self.assertIn("- 任務 ID: `OPS-REVIEW-PR-BODY-PRESERVE-001`", rendered)
        self.assertIn("- 標題: 保留人工整理的繁中 PR body", rendered)
        self.assertIn("- 摘要: 修正 Review Bus 覆寫問題", rendered)
        self.assertIn("- 狀態: `review`", rendered)
        self.assertIn("- 負責人: `Antigravity3`", rendered)
        self.assertIn("- 評審人: `Codex2`", rendered)
        self.assertIn("- 依賴任務: OPS-AGY-HARD-TIMEOUT-2H-001", rendered)
        self.assertIn("## 審查範圍", rendered)
        self.assertIn("## 分支資訊", rendered)
        self.assertIn("- 來源分支: `task/OPS-REVIEW-PR-BODY-PRESERVE-001`", rendered)
        self.assertIn("- 目標分支: `dev`", rendered)
        self.assertIn("## 下一步", rendered)
        self.assertIn("## 行動審查指南", rendered)
        self.assertIn("Orchestrator 會輪詢審查結果並自動同步回寫至 `ai-status.json`。", rendered)


class UpsertReviewPrPreserveBodyTests(unittest.TestCase):
    def setUp(self) -> None:
        github_bus.clear_remote_branch_snapshot_cache()
        self.config = {
            "github_bus": {
                "default_branch": "dev",
                "labels": {"review": ["pantheon-review"]},
                "templates": {"review_pr": ".orchestrator/templates/github_review_pr.md"},
            },
            "branch_workflow": {"enabled": True, "task_pr": {"target_branch": "dev"}},
        }
        self.task = {
            "id": "OPS-REVIEW-PR-BODY-PRESERVE-001",
            "title": "保留人工整理的繁中 PR body",
            "summary_zh": "修正 Review Bus 每次 reconcile 都覆寫既有 PR body 的行為",
            "status": "review",
            "owner": "Antigravity3",
            "reviewer": "Codex2",
            "depends_on": [],
            "artifacts": [".orchestrator/github_bus.py"],
            "next": "ready for review",
        }
        self.branch = "task/OPS-REVIEW-PR-BODY-PRESERVE-001"
        self.head_sha = "e" * 40

    def test_upsert_review_pr_preserves_human_body_on_reconcile(self) -> None:
        human_body = (
            "# 繁中 PR 說明\n"
            "人工撰寫的詳細改動說明。\n"
        )
        found_pr = {
            "number": 1050,
            "url": "https://github.com/alfloop-dev/odayplus/pull/1050",
            "title": f"[ReviewBus] {self.task['id']} {self.task['title']}",
            "headRefName": self.branch,
            "baseRefName": "dev",
            "state": "OPEN",
            "body": human_body,
        }
        bus_state = {"tasks": {}}

        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=self.branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value=self.head_sha),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=self.head_sha),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=found_pr),
            mock.patch.object(github_bus, "edit_pull_request_rest") as edit,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            changed = github_bus.upsert_review_pr(self.config, bus_state, {"tasks": []}, "o/r", self.task)

        self.assertTrue(changed)
        edit.assert_called_once()
        edited_body = edit.call_args.args[3]
        self.assertTrue(edited_body.startswith(human_body.strip()))
        self.assertIn("---", edited_body)
        self.assertIn("## 任務資訊", edited_body)
        self.assertIn(github_bus.COMMENT_MARKER, edited_body)
        self.assertIn(github_bus.BUS_END_MARKER, edited_body)

        # Reconcile consecutive run without change must be idempotent
        found_pr["body"] = edited_body
        with (
            mock.patch.object(github_bus, "review_branch_for_task", return_value=self.branch),
            mock.patch.object(github_bus, "branch_head_sha", return_value=self.head_sha),
            mock.patch.object(github_bus, "remote_branch_head_sha", return_value=self.head_sha),
            mock.patch.object(github_bus, "branch_has_diff", return_value=True),
            mock.patch.object(github_bus, "find_existing_pr", return_value=found_pr),
            mock.patch.object(github_bus, "edit_pull_request_rest") as edit_again,
            mock.patch.object(github_bus, "write_activity_log"),
        ):
            consecutive_changed = github_bus.upsert_review_pr(self.config, bus_state, {"tasks": []}, "o/r", self.task)

        self.assertFalse(consecutive_changed)
        edit_again.assert_not_called()


class MergeGroupReconciliationTests(unittest.TestCase):
    # A merge queue group head is a temporary merge commit GitHub builds on top
    # of the queue base; it is never the PR head. Fixtures keep the two SHAs
    # distinct so nothing can pass by conflating them.
    APPROVED_HEAD = "8eabc9734a000000000000000000000000000000"
    QUEUE_BASE_OLD = "a297b2a0aa000000000000000000000000000000"
    QUEUE_BASE_NEW = "b41f0c9dbb000000000000000000000000000000"
    GROUP_HEAD_OLD = "908e99fa00000000000000000000000000000000"
    GROUP_HEAD_NEW = "17393dd400000000000000000000000000000000"
    ADVANCED_HEAD = "c0ffee7700000000000000000000000000000000"

    def setUp(self) -> None:
        self.config = {
            "github_bus": {
                "enabled": True,
                "repo": "o/r",
                "poll_batch_sizes": {"merge_group_runs": 20},
            }
        }
        self.task = {
            "id": "ODP-TEST-MG-001",
            "title": "Merge group test task",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude2",
            "pr_number": 756,
            "approved_head": self.APPROVED_HEAD,
            # An approved task reached the merge queue through task_finalize.sh, so it
            # carries the verified submission that pins the reviewed commit. Recovery
            # after a merge_group failure is dispatched from these same facts.
            "review_submission": {
                "pr_number": 756,
                "branch": "task/odp-test-mg-001",
                "remote_sha": "8eabc9734a000000000000000000000000000000",
                "base_branch": "dev",
                "verified_at": "2026-09-04T00:00:00Z",
            },
            "next": "waiting for merge queue",
        }
        self.status = {"tasks": [self.task], "handoffs": []}
        self.bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
        # The GraphQL node github_bus.fetch_pr_merge_queue_status returns: one
        # reader, carrying both PR facts and queue enrollment.
        self.default_pr_node = {
            "number": 756,
            "state": "OPEN",
            "merged": False,
            "mergedAt": None,
            "headRefOid": self.APPROVED_HEAD,
            "isInMergeQueue": False,
            "mergeQueueEntry": None,
        }

    # ------------------------------------------------------------------
    # GitHub transport stub
    # ------------------------------------------------------------------

    def _gh(self, *, pr_node="default", pr_nodes=None, runs=(), commits=None):
        """Answer `gh` calls with GitHub-shaped payloads.

        Reconciliation is driven through its real readers -- the GraphQL PR
        node, the `actions/runs` list, `repos/{repo}/actions/runs/{id}` and
        `repos/{repo}/commits/{sha}` -- rather than by patching the guard's own
        helpers out. A negative case therefore fails for the reason GitHub
        would actually produce.

        Pass an exception instance for `pr_node` / `runs` to model an API that
        cannot answer; omit a SHA from `commits` to model a commit lookup that
        cannot answer. `pr_nodes` supplies one node per GraphQL read in order
        (the last repeats), so facts A and facts B can disagree the way a PR
        that moves mid-reconciliation would.

        `commits` values may be a raw payload dict, which is passed through
        verbatim so a damaged `parents` shape can be modelled.
        """
        pr_node = self.default_pr_node if pr_node == "default" else pr_node
        sequence = list(pr_nodes) if pr_nodes is not None else None
        base_commits = {
            self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD],
        }
        if commits is not None:
            resolved_commits = dict(base_commits)
            resolved_commits.update(commits)
            commits = resolved_commits
        else:
            commits = base_commits
        reads = {"pr": 0}

        def _call(args, **kwargs):
            if args[:2] == ["api", "graphql"]:
                if sequence is not None:
                    node = sequence[min(reads["pr"], len(sequence) - 1)]
                    reads["pr"] += 1
                else:
                    node = pr_node
                if isinstance(node, BaseException):
                    raise node
                return {"data": {"repository": {"pullRequest": node}}}
            if args[:1] == ["api"]:
                path = str(args[1]) if len(args) > 1 else ""
                if "actions/runs?" in path:
                    if isinstance(runs, BaseException):
                        raise runs
                    return {"total_count": len(runs), "workflow_runs": list(runs)}
                if "/actions/runs/" in path:
                    if isinstance(runs, BaseException):
                        raise runs
                    wanted = path.rsplit("/", 1)[-1]
                    found = next(
                        (r for r in runs if str(r.get("id")) == wanted), None
                    )
                    if found is None:
                        raise github_bus.GitHubBusError(f"run {wanted} not found")
                    return found
                if "/commits/" in path:
                    sha = path.rsplit("/", 1)[-1]
                    parents = commits.get(sha)
                    if isinstance(parents, BaseException):
                        raise parents
                    if parents is None:
                        raise github_bus.GitHubBusError(f"commit {sha} not found")
                    if isinstance(parents, dict):
                        return parents
                    return {"sha": sha, "parents": [{"sha": p} for p in parents]}
            return None

        return _call

    def _failure_run(self, **overrides):
        run = {
            "id": 34003742537,
            "head_branch": "refs/heads/gh-readonly-queue/dev/pr-756-a297b2a0",
            "head_sha": self.GROUP_HEAD_OLD,
            "conclusion": "failure",
            "status": "completed",
            "created_at": "2026-09-06T01:25:00Z",
            "run_started_at": "2026-09-06T01:25:00Z",
            "html_url": "https://github.com/o/r/actions/runs/34003742537",
            "event": "merge_group",
            "name": "CI",
            "workflow_id": 100,
        }
        run.update(overrides)
        return run

    def _newer_run(self, **overrides):
        run = {
            "id": 34004041183,
            "head_branch": "refs/heads/gh-readonly-queue/dev/pr-756-b41f0c9d",
            "head_sha": self.GROUP_HEAD_NEW,
            "conclusion": None,
            "status": "in_progress",
            "created_at": "2026-09-06T01:29:54Z",
            "run_started_at": "2026-09-06T01:29:54Z",
            "event": "merge_group",
            "name": "CI",
            "workflow_id": 100,
        }
        run.update(overrides)
        return run

    def _group_enrolling_approved_head(self, base=None):
        """Parents of a group commit that enrolled the reviewed head."""
        return [base or self.QUEUE_BASE_NEW, self.APPROVED_HEAD]

    @staticmethod
    def _log_types(write_log):
        return [call.args[1]["type"] for call in write_log.call_args_list]

    # ------------------------------------------------------------------
    # Parsing and correlation
    # ------------------------------------------------------------------

    def test_parse_merge_group_pr_number(self) -> None:
        parse = github_reconciliation.parse_merge_group_pr_number
        self.assertEqual(
            parse("refs/heads/gh-readonly-queue/dev/pr-756-8eabc973"),
            756,
        )
        self.assertEqual(
            parse("gh-readonly-queue/main/pr-42-0123456789abcdef"),
            42,
        )
        self.assertIsNone(parse("gh-readonly-queue/dev/pr-invalid-abc"))
        self.assertIsNone(parse("refs/heads/feature/test-branch"))
        # A PR-shaped suffix in an arbitrary branch must never be attributed to
        # a merge queue run.
        self.assertIsNone(parse("refs/heads/feature/pr-756-deadbeef"))
        self.assertIsNone(parse("gh-readonly-queue/dev/pr-756-deadbeef/extra"))
        self.assertIsNone(parse(""))
        self.assertIsNone(parse(None))

    def test_parse_merge_group_rejects_bare_pr_ref(self) -> None:
        """A PR-shaped bare ref is not proof that a run came from merge queue."""
        self.assertIsNone(
            github_reconciliation.parse_merge_group_pr_number("pr-100-abc1234")
        )

    def test_save_bus_state_retains_merge_group_failure_audit_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-github-bus-state-") as tmp:
            state_path = Path(tmp) / "github-bus-state.json"
            config = {"paths": {"github_bus_state": str(state_path)}}
            state = {
                "tasks": {
                    "ODP-TEST-MG-001": {
                        "last_merge_group_failure": {
                            "run_id": 31321422749,
                            "queue_ref": "gh-readonly-queue/dev/pr-756-8eabc973",
                            "head_sha": self.GROUP_HEAD_OLD,
                            "pr_number": 756,
                        }
                    }
                }
            }

            github_bus.save_bus_state(config, state)

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["tasks"]["ODP-TEST-MG-001"]["last_merge_group_failure"]["run_id"],
                31321422749,
            )

    def test_correlate_merge_group_task(self) -> None:
        correlate = github_reconciliation.correlate_merge_group_task
        # Direct task.pr_number match
        task, reason = correlate(self.status, 756, self.bus_state)
        self.assertEqual(reason, "matched")
        self.assertEqual(task["id"], "ODP-TEST-MG-001")

        # review_submission.pr_number match
        status_sub = {
            "tasks": [
                {
                    "id": "ODP-SUB-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "review_submission": {"pr_number": 888},
                }
            ]
        }
        task, reason = correlate(status_sub, 888, self.bus_state)
        self.assertEqual(reason, "matched")
        self.assertEqual(task["id"], "ODP-SUB-001")

        # merge_route.pr_number match
        status_route = {
            "tasks": [
                {
                    "id": "ODP-ROUTE-001",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "merge_route": {"pr_number": 999},
                }
            ]
        }
        task, reason = correlate(status_route, 999, self.bus_state)
        self.assertEqual(reason, "matched")
        self.assertEqual(task["id"], "ODP-ROUTE-001")

        # bus_state review_pr.number match
        status_bus = {
            "tasks": [
                {
                    "id": "ODP-BUS-001",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }
        bus_state = {"tasks": {"ODP-BUS-001": {"review_pr": {"number": 111}}}}
        task, reason = correlate(status_bus, 111, bus_state)
        self.assertEqual(reason, "matched")
        self.assertEqual(task["id"], "ODP-BUS-001")

        # Unmatched
        task, reason = correlate(self.status, 99999, self.bus_state)
        self.assertEqual(reason, "unmatched")
        self.assertIsNone(task)

        # Ambiguous (multiple tasks claiming same PR)
        status_dup = {
            "tasks": [
                {"id": "TASK-A", "pr_number": 500},
                {"id": "TASK-B", "pr_number": 500},
            ]
        }
        task, reason = correlate(status_dup, 500, self.bus_state)
        self.assertEqual(reason, "ambiguous")
        self.assertIsNone(task)

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def test_merge_queue_reader_carries_pr_state_and_head(self) -> None:
        """One reader answers both "is it queued" and "is it still on the head"."""
        for field in ("state", "merged", "mergedAt", "headRefOid", "isInMergeQueue"):
            self.assertIn(field, github_bus.PR_MERGE_QUEUE_GRAPHQL_QUERY)

        with mock.patch("github_bus.gh_json", side_effect=self._gh()):
            facts = github_reconciliation.fetch_pr_queue_facts("o/r", 756)
        self.assertEqual(github_reconciliation.pr_state(facts), "OPEN")
        self.assertEqual(github_reconciliation.pr_head_sha(facts), self.APPROVED_HEAD)
        self.assertFalse(github_bus.is_in_merge_queue(facts))

    def test_fetch_pr_queue_facts_offline_propagates_and_errors_are_unknown(self) -> None:
        with mock.patch("github_bus.gh_json", side_effect=github_bus.GitHubBusOffline("offline")):
            with self.assertRaises(github_bus.GitHubBusOffline):
                github_reconciliation.fetch_pr_queue_facts("o/r", 756)

        with mock.patch("github_bus.gh_json", side_effect=github_bus.GitHubBusError("api error")):
            self.assertIsNone(github_reconciliation.fetch_pr_queue_facts("o/r", 756))

        with mock.patch("github_bus.gh_json", return_value=["not a dict"]):
            self.assertIsNone(github_reconciliation.fetch_pr_queue_facts("o/r", 756))

        # A PR node GitHub could not populate is unknown, not "closed".
        with mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node={"number": 756})):
            self.assertIsNone(github_reconciliation.fetch_pr_queue_facts("o/r", 756))

    def test_fetch_merge_group_runs_propagates_offline(self) -> None:
        with mock.patch("github_bus.gh_json", side_effect=github_bus.GitHubBusOffline("gh offline")):
            with self.assertRaises(github_bus.GitHubBusOffline):
                github_reconciliation.fetch_merge_group_runs("o/r")

    def test_fetch_merge_group_runs_distinguishes_empty_from_unanswered(self) -> None:
        """`[]` means GitHub reported no runs; None means it did not answer."""
        with mock.patch("github_bus.gh_json", return_value={"workflow_runs": []}):
            self.assertEqual(github_reconciliation.fetch_merge_group_runs("o/r"), [])

        with mock.patch("github_bus.gh_json", side_effect=github_bus.GitHubBusError("500")):
            self.assertIsNone(github_reconciliation.fetch_merge_group_runs("o/r"))

        with mock.patch("github_bus.gh_json", return_value=None):
            self.assertIsNone(github_reconciliation.fetch_merge_group_runs("o/r"))

        with mock.patch("github_bus.gh_json", return_value={"workflow_runs": "nope"}):
            self.assertIsNone(github_reconciliation.fetch_merge_group_runs("o/r"))

    def test_fetch_commit_parent_shas_tri_state(self) -> None:
        commits = {
            self.GROUP_HEAD_NEW: self._group_enrolling_approved_head(),
            self.GROUP_HEAD_OLD: None,
            # A parents array GitHub could not populate. `[{}]` is a damaged
            # payload, not a commit with no parents.
            "broken" + "0" * 34: {"sha": "broken", "parents": [{}]},
            "empty" + "0" * 35: {"sha": "empty", "parents": []},
            "nolist" + "0" * 34: {"sha": "nolist", "parents": "nope"},
        }
        with mock.patch("github_bus.gh_json", side_effect=self._gh(commits=commits)):
            self.assertEqual(
                github_reconciliation.fetch_commit_parent_shas("o/r", self.GROUP_HEAD_NEW),
                [self.QUEUE_BASE_NEW, self.APPROVED_HEAD],
            )
            # Unknown commit: GitHub could not answer.
            self.assertIsNone(
                github_reconciliation.fetch_commit_parent_shas("o/r", self.GROUP_HEAD_OLD)
            )
            for sha in ("broken" + "0" * 34, "empty" + "0" * 35, "nolist" + "0" * 34):
                self.assertIsNone(
                    github_reconciliation.fetch_commit_parent_shas("o/r", sha), sha
                )
        self.assertIsNone(github_reconciliation.fetch_commit_parent_shas("o/r", ""))

    # ------------------------------------------------------------------
    # Fact predicates
    # ------------------------------------------------------------------

    def test_is_valid_pr_facts(self) -> None:
        valid_facts = {"state": "OPEN", "headRefOid": self.APPROVED_HEAD}
        self.assertTrue(github_reconciliation.is_valid_pr_facts(valid_facts))
        self.assertFalse(github_reconciliation.is_valid_pr_facts({}))
        self.assertFalse(github_reconciliation.is_valid_pr_facts(None))
        self.assertFalse(github_reconciliation.is_valid_pr_facts({"state": ""}))
        self.assertFalse(github_reconciliation.is_valid_pr_facts({"state": "OPEN"}))
        self.assertFalse(github_reconciliation.is_valid_pr_facts({"headRefOid": "8eabc973"}))

    def test_pr_state_accepts_only_github_states(self) -> None:
        """Any other string is unknown -- never silently read as closed."""
        for state in ("OPEN", "open", " Closed ", "MERGED"):
            self.assertIn(
                github_reconciliation.pr_state({"state": state}),
                github_reconciliation.VALID_PR_STATES,
            )
        for state in ("UNKNOWN", "LOCKED", "DRAFT", "", None, 3, "OPEN CLOSED"):
            self.assertEqual(github_reconciliation.pr_state({"state": state}), "")
            self.assertFalse(
                github_reconciliation.is_valid_pr_facts(
                    {"state": state, "headRefOid": self.APPROVED_HEAD}
                )
            )

    def test_pr_is_merged_reads_every_merged_signal(self) -> None:
        self.assertTrue(github_reconciliation.pr_is_merged({"state": "MERGED"}))
        self.assertTrue(github_reconciliation.pr_is_merged({"state": "CLOSED", "merged": True}))
        self.assertTrue(
            github_reconciliation.pr_is_merged({"state": "CLOSED", "mergedAt": "2026-09-06T01:54:20Z"})
        )
        self.assertFalse(github_reconciliation.pr_is_merged({"state": "OPEN", "merged": False}))
        self.assertFalse(github_reconciliation.pr_is_merged({"state": "CLOSED"}))

    def test_match_workflow_identity_is_tri_state(self) -> None:
        """Unidentifiable is not "different" -- it is an open question."""
        match_wf = github_reconciliation.match_workflow_identity
        # Matching workflow_id
        self.assertIs(match_wf({"workflow_id": 100}, {"workflow_id": 100}), True)
        self.assertIs(match_wf({"workflow_id": 100}, {"workflow_id": 200}), False)
        # Matching workflow name when workflow_id is absent
        self.assertIs(match_wf({"name": "CI"}, {"name": "CI"}), True)
        self.assertIs(match_wf({"name": "CI"}, {"name": "Deploy"}), False)
        # Missing identity on either side is unknown, not a mismatch.
        self.assertIsNone(match_wf({}, {}))
        self.assertIsNone(match_wf({"workflow_id": 100}, {}))
        self.assertIsNone(match_wf({}, {"workflow_id": 100}))
        self.assertIsNone(match_wf({"name": "CI"}, {}))

    def test_compare_run_recency_is_tri_state(self) -> None:
        compare = github_reconciliation.compare_run_recency
        self.assertIs(compare({"id": 1}, {"id": 2}), True)
        self.assertIs(compare({"id": 2}, {"id": 1}), False)
        self.assertIs(
            compare(
                {"id": "a", "created_at": "2026-09-06T01:25:00Z"},
                {"id": "b", "created_at": "2026-09-06T01:29:00Z"},
            ),
            True,
        )
        # Nothing comparable: the ordering is an open question.
        self.assertIsNone(compare({"id": "a"}, {"id": "b"}))
        self.assertIsNone(compare({}, {}))

    def test_merge_group_incorporates_head_requires_direct_parenthood(self) -> None:
        """Association is parenthood, not ancestry.

        A group built for an advanced head B still *contains* the earlier head A
        as an ancestor, so an ancestry test would report that group as covering
        A. Only the group's parent list names the head it actually enrolled.
        """
        incorporates = github_reconciliation.merge_group_incorporates_head
        base_only = "ba5e0000" + "0" * 32
        single_parent = "51g10000".replace("g", "9") + "0" * 32
        commits = {
            # Group that enrolled the reviewed head.
            self.GROUP_HEAD_NEW: [self.QUEUE_BASE_NEW, self.APPROVED_HEAD],
            # Group that enrolled a later head of the same PR. APPROVED_HEAD is
            # an ancestor of ADVANCED_HEAD but was not enrolled here.
            self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.ADVANCED_HEAD],
            # The reviewed head is this group's BASE, not something it
            # enrolled: the group was merely built on top of it.
            base_only: [self.APPROVED_HEAD, self.ADVANCED_HEAD],
            # Not the merge-commit shape a group head has.
            single_parent: [self.APPROVED_HEAD],
            # A parents payload that does not answer.
            "brokenpp" + "0" * 32: {"sha": "brokenpp", "parents": [{}, {}]},
        }
        with mock.patch("github_bus.gh_json", side_effect=self._gh(commits=commits)):
            self.assertIs(
                incorporates("o/r", self.GROUP_HEAD_NEW, self.APPROVED_HEAD, {}), True
            )
            self.assertIs(
                incorporates("o/r", self.GROUP_HEAD_OLD, self.APPROVED_HEAD, {}), False
            )
            # First parent is the base. Matching it would credit a group merely
            # built on top of the reviewed head to the PR that produced it.
            self.assertIs(incorporates("o/r", base_only, self.APPROVED_HEAD, {}), False)
            # Structure that cannot answer: unknown, not "no".
            self.assertIsNone(
                incorporates("o/r", single_parent, self.APPROVED_HEAD, {})
            )
            self.assertIsNone(
                incorporates("o/r", "brokenpp" + "0" * 32, self.APPROVED_HEAD, {})
            )
            # Commit GitHub cannot answer for: unknown, not "no".
            self.assertIsNone(
                incorporates("o/r", "deadbeef" + "0" * 32, self.APPROVED_HEAD, {})
            )
        # Missing SHAs are a definitive no, and are answered without any call.
        self.assertIs(incorporates("o/r", "", self.APPROVED_HEAD, {}), False)
        self.assertIs(incorporates("o/r", self.GROUP_HEAD_NEW, "", {}), False)
        # A group head equal to the PR head is not evidence GitHub can produce.
        self.assertIs(
            incorporates("o/r", self.APPROVED_HEAD, self.APPROVED_HEAD, {}), False
        )

    def test_merge_group_incorporates_head_caches_commit_lookups(self) -> None:
        cache = {}
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}
        with mock.patch("github_bus.gh_json", side_effect=self._gh(commits=commits)) as gh_json:
            for _ in range(3):
                github_reconciliation.merge_group_incorporates_head(
                    "o/r", self.GROUP_HEAD_NEW, self.APPROVED_HEAD, cache
                )
        self.assertEqual(gh_json.call_count, 1)

    def test_find_superseding_run_reports_unknown_association(self) -> None:
        """An unanswered candidate is an open question, not a "no"."""
        target = self._failure_run()
        candidate = self._newer_run()
        with mock.patch("github_bus.gh_json", side_effect=self._gh(commits={})):
            found, reason, unknown = github_reconciliation.find_superseding_merge_group_run(
                target, [target, candidate], 756, self.APPROVED_HEAD, "o/r"
            )
        self.assertIsNone(found)
        self.assertIsNone(reason)
        self.assertTrue(unknown)

    def test_find_superseding_run_ignores_sibling_run_of_the_failing_group(self) -> None:
        """A second run over the same group commit is not a newer group."""
        target = self._failure_run()
        sibling = self._failure_run(
            id=34003742999,
            conclusion="success",
            status="completed",
            created_at="2026-09-06T01:26:00Z",
        )
        commits = {self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD]}
        with mock.patch("github_bus.gh_json", side_effect=self._gh(commits=commits)) as gh_json:
            found, reason, unknown = github_reconciliation.find_superseding_merge_group_run(
                target, [target, sibling], 756, self.APPROVED_HEAD, "o/r"
            )
        self.assertIsNone(found)
        self.assertIsNone(reason)
        self.assertFalse(unknown)
        gh_json.assert_not_called()

    # ------------------------------------------------------------------
    # Genuine failure -> reviewer recovery
    # ------------------------------------------------------------------

    def test_reconcile_merge_group_failure_creates_audit_log_and_recovery_handoff(self) -> None:
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True) as commit_trans,
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check") as emit_gate,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )

        self.assertTrue(changed)
        # 1. Auditable activity log recorded with exact run, queue ref, head SHA, PR number, task ID
        write_log.assert_called_once()
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_reconciled")
        self.assertEqual(log_entry["task_id"], "ODP-TEST-MG-001")
        self.assertEqual(log_entry["run_id"], 31321422749)
        self.assertEqual(log_entry["queue_ref"], "refs/heads/gh-readonly-queue/dev/pr-756-a297b2a0")
        self.assertEqual(log_entry["head_sha"], self.GROUP_HEAD_OLD)
        self.assertEqual(log_entry["pr_number"], 756)
        self.assertEqual(log_entry["conclusion"], "failure")

        # 2. Reviewer recovery handoff dispatched to assigned reviewer Claude2
        self.assertEqual(len(self.status["handoffs"]), 1)
        handoff = self.status["handoffs"][0]
        self.assertEqual(handoff["task_id"], "ODP-TEST-MG-001")
        self.assertEqual(handoff["to"], "Claude2")
        self.assertEqual(handoff["from"], "Codex")
        self.assertEqual(handoff["status"], "pending")
        self.assertEqual(handoff["reason"], "merge_group_failure")
        self.assertEqual(handoff["run_id"], 31321422749)

        # 3. Product task canonically transitions to review status with approved_head cleared
        self.assertEqual(self.task["status"], "review")
        self.assertIsNone(self.task.get("approved_head"))
        self.assertIn("reviewer recovery handoff dispatched", self.task["next"])

        # 4. Bus state records correlation and marks run processed
        self.assertIn(
            "merge_group_run:31321422749",
            self.bus_state["processed_merge_group_run_ids"],
        )
        task_bus = self.bus_state["tasks"]["ODP-TEST-MG-001"]["last_merge_group_failure"]
        self.assertEqual(task_bus["run_id"], 31321422749)
        self.assertEqual(task_bus["pr_number"], 756)

        commit_trans.assert_called_once_with(self.config, self.status)
        # The exact reviewed head the failing group enrolled binds the cause.
        self.assertEqual(handoff["source_head"], self.APPROVED_HEAD)
        # The gate is not emitted from this snapshot (the caller reloads it
        # away); the revocation intent rides the committed transition instead.
        emit_gate.assert_not_called()
        self.assertTrue(self.task["review_gate_refresh_pending"])
        self.assertEqual(
            [t["sha"] for t in self.task["review_gate_revocations"]], [self.GROUP_HEAD_OLD]
        )

    def test_reconcile_merge_group_failure_clears_and_reemits_pending_review_gate(self) -> None:
        self.task["review_gate_sha"] = self.task["approved_head"]
        run_fixture = self._failure_run(id=31321422750)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check") as emit_gate,
            mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertNotIn("approved_head", self.task)
        self.assertNotIn("review_gate_sha", self.task)
        # The confirmed PR-head grant and the failing group commit are both
        # retained as revocation targets in the committed transition, for the
        # canonical sync to re-emit as pending.
        emit_gate.assert_not_called()
        self.assertTrue(self.task["review_gate_refresh_pending"])
        self.assertEqual(
            sorted(
                (t["repo_slug"], t["sha"], t["context"])
                for t in self.task["review_gate_revocations"]
            ),
            sorted([
                ("o/r", self.APPROVED_HEAD, "task-review-gate"),
                ("o/r", self.GROUP_HEAD_OLD, "task-review-gate"),
            ]),
        )

    def test_reconcile_merge_group_failure_stale_snapshot_reject_does_not_mark_run_processed(self) -> None:
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=False) as commit_trans,
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check") as emit_gate,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )

        # When commit fails closed due to stale snapshot, return False and do NOT mark run processed
        self.assertFalse(changed)
        commit_trans.assert_called_once()
        emit_gate.assert_not_called()
        self.assertNotIn(
            "merge_group_run:31321422749",
            self.bus_state.get("processed_merge_group_run_ids", []),
        )
        self.assertNotIn(
            "last_merge_group_failure",
            self.bus_state.get("tasks", {}).get("ODP-TEST-MG-001", {}),
        )

    def _bind_real_source_range(self) -> None:
        """Back the approved PR head with a real synthetic range and registered actors.

        Codex writes the task commit, `dev` advances, and Codex's finalizer
        merges it back; the PR head is that merge, never the merge-group head
        GitHub builds on top of the queue base. The submission's source
        provenance is produced exactly as `submit_review` produces it -- the
        delivery-identity scanner over the validated range, recorded by
        `review_submission_source_evidence` -- and the reviewer Claude2 is a
        registered agent in a pool of its own, so the source-author rule reads
        the range from Git and finds it genuinely independent.
        """
        tmp = tempfile.TemporaryDirectory(prefix="test-mg-source-")
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "source-git"
        repo.mkdir()
        task_id = self.task["id"]
        branch = self.task["review_submission"]["branch"]

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "-q", "-b", "dev")
        git("config", "user.email", "worker@pantheon.local")
        git("config", "user.name", "Worker")
        git("commit", "-q", "--allow-empty", "-m", "bootstrap")
        git("checkout", "-q", "-b", branch)
        git(
            "commit", "-q", "--allow-empty", "-m",
            f"{task_id}: merge group change\n\nLLM-Agent: Codex\nTask-ID: {task_id}\nReviewer: Claude2\n",
        )
        git("checkout", "-q", "dev")
        git("commit", "-q", "--allow-empty", "-m", "OTHER-001: unrelated dev work")
        git("update-ref", "refs/remotes/origin/dev", "dev")
        git("checkout", "-q", branch)
        git("merge", "--no-ff", "-q", "-m", f"Merge dev into {branch}\n\nLLM-Agent: Codex\n", "dev")
        head = git("rev-parse", "HEAD")

        identity = inspect_delivery_identity(
            repo, task_id=task_id, base="dev", head=head, expected_branch=branch, actual_branch=branch
        )
        self.assertEqual(identity.errors, [])
        self.APPROVED_HEAD = head
        self.assertNotEqual(head, self.GROUP_HEAD_OLD)
        self.task["branch"] = branch
        self.task["approved_head"] = head
        self.task["review_submission"].update(
            {
                "remote_sha": head,
                **ai_status.review_submission_source_evidence(identity),
                "submitted_by": "Codex",
            }
        )
        self.default_pr_node["headRefOid"] = head
        self.config.update(
            {
                "coordination": {"repositories": {"pantheon": {"local_path": str(repo)}}},
                "branch_workflow": {"dev_branch": "dev"},
                "agents": {
                    "codex": {"display_name": "Codex", "provider": "codex", "account_pool": "codex_bjoe"},
                    "claude2": {"display_name": "Claude2", "provider": "claude", "account_pool": "claude_two"},
                },
            }
        )

    def test_reconcile_merge_group_failure_normalize_handoffs_preserves_reviewer_recovery(self) -> None:
        self._bind_real_source_range()
        self.assertEqual(self.task["review_submission"]["source_authors"], ["Codex"])
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )

        self.assertTrue(changed)
        # Execute the real normalize_handoffs function (not a mock)
        ai_status.normalize_handoffs(self.status)

        # The pending reviewer recovery handoff must be preserved (not marked done)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)
        handoff = self.status["handoffs"][0]
        self.assertEqual(handoff["status"], "pending")
        self.assertEqual(handoff["to"], "Claude2")
        self.assertEqual(handoff["reason"], "merge_group_failure")

        # Dispatch engine must evaluate priority 0 (REASON_REVIEW_READY) for reviewer
        # Claude2. Reviewer recovery goes through the same single exact-head CI
        # readiness predicate as any other review dispatch: the merge_group run that
        # failed ran on the queue ref, not on the PR head, so the PR head keeps its
        # successful required CI and still matches the verified submission SHA.
        head_sha = self.task["review_submission"]["remote_sha"]
        with (
            mock.patch.object(ai_status, "resolve_task_sha", return_value=head_sha),
            mock.patch.object(
                ai_status, "task_pr_ci_status", return_value=("OPEN", "success")
            ),
        ):
            priority = dispatch_engine.dispatch_priority_for_task(
                self.config, self.task, "Claude2"
            )
        self.assertEqual(priority, 0)

        # That predicate stays fail-closed on the recovery path too: pending required
        # CI on the exact head must not consume the reviewer slot.
        with (
            mock.patch.object(ai_status, "resolve_task_sha", return_value=head_sha),
            mock.patch.object(
                ai_status, "task_pr_ci_status", return_value=("OPEN", "pending")
            ),
        ):
            self.assertIsNone(
                dispatch_engine.dispatch_priority_for_task(
                    self.config, self.task, "Claude2"
                )
            )

    def test_reconcile_merge_group_duplicate_is_strictly_idempotent(self) -> None:
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            first = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )
            self.assertTrue(first)
            self.assertEqual(write_log.call_count, 1)
            self.assertEqual(len(self.status["handoffs"]), 1)

            # Second call with same duplicate run
            second = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )
            self.assertFalse(second)
            self.assertEqual(write_log.call_count, 1)
            self.assertEqual(len(self.status["handoffs"]), 1)

    # ------------------------------------------------------------------
    # Stale failures: proven superseded or proven finished
    # ------------------------------------------------------------------

    def test_reconcile_merge_group_superseded_by_pending_run_is_stale_and_non_mutating(self) -> None:
        """Older cancelled run processed after a newer group already started pending.

        This is the PR #1212 incident: group 34003742537 settled `cancelled` at
        01:33 while group 34004041183, which had enrolled the same reviewed head
        at 01:29, was still running and later merged the PR.
        """
        old_cancelled_run = self._failure_run(conclusion="cancelled")
        new_pending_run = self._newer_run()
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}

        # Order must not matter: the failure is reconciled against a fresh
        # snapshot, not against whatever happened to precede it in the batch.
        for runs in ([new_pending_run, old_cancelled_run], [old_cancelled_run, new_pending_run]):
            with self.subTest(order=[r["id"] for r in runs]):
                bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
                status_obj = {"tasks": [dict(self.task)], "handoffs": []}

                with (
                    mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
                    mock.patch("github_reconciliation.write_activity_log") as write_log,
                    mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
                ):
                    changed = github_reconciliation.reconcile_merge_group_runs(
                        self.config, bus_state, status_obj, "o/r", runs
                    )

                self.assertFalse(changed)
                commit_trans.assert_not_called()
                # Task status must NOT be demoted, approved_head must remain intact
                self.assertEqual(status_obj["tasks"][0]["status"], "review_approved")
                self.assertEqual(status_obj["tasks"][0]["approved_head"], self.APPROVED_HEAD)
                self.assertEqual(len(status_obj["handoffs"]), 0)
                # Stale audit log recorded
                stale_logs = [
                    call.args[1]
                    for call in write_log.call_args_list
                    if call.args[1]["type"] == "merge_group_failure_stale"
                ]
                self.assertEqual(len(stale_logs), 1)
                self.assertEqual(stale_logs[0]["run_id"], 34003742537)
                self.assertEqual(stale_logs[0]["reason"], "pending_group")
                self.assertEqual(stale_logs[0]["superseded_by_run_id"], 34004041183)
                # Old cancelled run is recorded in processed IDs for deduplication
                self.assertIn("merge_group_run:34003742537", bus_state["processed_merge_group_run_ids"])

    def test_reconcile_merge_group_superseded_by_success_run_is_stale_and_non_mutating(self) -> None:
        old_failed_run = self._failure_run()
        new_success_run = self._newer_run(conclusion="success", status="completed")
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}
        runs = [old_failed_run, new_success_run]

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertIn("merge_group_run:34003742537", self.bus_state["processed_merge_group_run_ids"])
        self.assertIn("merge_group_run:34004041183", self.bus_state["processed_merge_group_run_ids"])
        stale_logs = [
            call.args[1]
            for call in write_log.call_args_list
            if call.args[1]["type"] == "merge_group_failure_stale"
        ]
        self.assertEqual(stale_logs[0]["reason"], "successful_group")

    def test_reconcile_merge_group_pr_already_merged_is_stale_and_non_mutating(self) -> None:
        run_fixture = self._failure_run(conclusion="cancelled")
        merged_node = dict(
            self.default_pr_node,
            state="MERGED",
            merged=True,
            mergedAt="2026-09-06T01:54:20Z",
        )

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=merged_node)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertIn("merge_group_run:34003742537", self.bus_state["processed_merge_group_run_ids"])
        stale_log = write_log.call_args.args[1]
        self.assertEqual(stale_log["type"], "merge_group_failure_stale")
        self.assertEqual(stale_log["reason"], "already_merged")

    def test_reconcile_merge_group_pr_closed_unmerged_is_stale_and_non_mutating(self) -> None:
        run_fixture = self._failure_run()
        closed_node = dict(self.default_pr_node, state="CLOSED")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=closed_node)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertIn("merge_group_run:34003742537", self.bus_state["processed_merge_group_run_ids"])
        self.assertEqual(write_log.call_args.args[1]["reason"], "pr_state_closed")

    # ------------------------------------------------------------------
    # A newer run must never mask a genuine failure
    # ------------------------------------------------------------------

    def test_reconcile_merge_group_different_pr_does_not_mask_genuine_failure(self) -> None:
        failed_run_pr756 = self._failure_run(id=31321422749)
        newer_success_pr757 = self._newer_run(
            id=31321422999,
            head_branch="refs/heads/gh-readonly-queue/dev/pr-757-b41f0c9d",
            conclusion="success",
            status="completed",
        )
        runs = [failed_run_pr756, newer_success_pr757]
        # Even if that other PR's group did enrol this head, it is a different PR.
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)

    def test_reconcile_merge_group_different_workflow_does_not_mask_genuine_failure(self) -> None:
        failed_run_ci = self._failure_run(id=31321422749)
        newer_success_docs = self._newer_run(
            id=31321422999,
            conclusion="success",
            status="completed",
            name="Deploy Docs",
            workflow_id=200,
        )
        runs = [failed_run_ci, newer_success_docs]
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)

    def test_reconcile_merge_group_newer_group_for_advanced_head_does_not_mask_failure(self) -> None:
        """A group built for a later head of the same PR is not this head's group.

        The reviewed head is an ancestor of the advanced head, so an ancestry
        test would wrongly report the newer group as covering it.
        """
        old_failed_run = self._failure_run()
        new_pending_run = self._newer_run()
        runs = [old_failed_run, new_pending_run]
        commits = {self.GROUP_HEAD_NEW: [self.QUEUE_BASE_NEW, self.ADVANCED_HEAD]}

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)
        self.assertIn("merge_group_failure_reconciled", self._log_types(write_log))

    def test_reconcile_merge_group_candidate_missing_fields_is_unknown_not_demote(self) -> None:
        """A same-PR candidate GitHub described incompletely is an open question.

        Missing head SHA, unidentifiable workflow, uncomparable ordering and a
        damaged parents payload all used to be skipped, which collapsed to
        "no candidate exists" and let the failure revoke the approval. Each is
        now unknown: retained, un-processed, asked again next cycle.
        """
        cases = {
            "missing_head_sha": (
                self._newer_run(head_sha=""),
                {self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD]},
            ),
            "unidentifiable_workflow": (
                self._newer_run(workflow_id=None, name=""),
                {
                    self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD],
                    self.GROUP_HEAD_NEW: self._group_enrolling_approved_head(),
                },
            ),
            "uncomparable_order": (
                self._newer_run(id="not-a-number", created_at=None, run_started_at=None),
                {
                    self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD],
                    self.GROUP_HEAD_NEW: self._group_enrolling_approved_head(),
                },
            ),
            "broken_parents_payload": (
                self._newer_run(),
                {
                    self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD],
                    self.GROUP_HEAD_NEW: {"sha": self.GROUP_HEAD_NEW, "parents": [{}]},
                },
            ),
        }
        for label, (candidate, commits) in cases.items():
            with self.subTest(case=label):
                bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
                status_obj = {"tasks": [dict(self.task)], "handoffs": []}
                target = self._failure_run(
                    workflow_id=None if label == "unidentifiable_workflow" else 100,
                    name="" if label == "unidentifiable_workflow" else "CI",
                )
                runs = [target, candidate]

                with (
                    mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
                    mock.patch("github_reconciliation.write_activity_log") as write_log,
                    mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
                ):
                    changed = github_reconciliation.reconcile_merge_group_runs(
                        self.config, bus_state, status_obj, "o/r", runs
                    )

                self.assertFalse(changed)
                commit_trans.assert_not_called()
                self.assertEqual(status_obj["tasks"][0]["status"], "review_approved")
                self.assertEqual(status_obj["tasks"][0]["approved_head"], self.APPROVED_HEAD)
                self.assertEqual(len(status_obj["handoffs"]), 0)
                self.assertNotIn(
                    "merge_group_run:34003742537",
                    bus_state.get("processed_merge_group_run_ids", []),
                )
                log_entry = write_log.call_args.args[1]
                self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
                self.assertEqual(log_entry["reason"], "group_association_unknown")

    def test_reconcile_merge_group_base_parent_match_does_not_supersede_failure(self) -> None:
        """The reviewed head as a group's BASE parent is not enrolment evidence.

        A group built on top of an already-merged head carries that head as its
        first parent. Crediting it would let the next PR's group suppress this
        PR's failure.
        """
        old_failed_run = self._failure_run()
        new_pending_run = self._newer_run()
        runs = [old_failed_run, new_pending_run]
        commits = {self.GROUP_HEAD_NEW: [self.APPROVED_HEAD, self.ADVANCED_HEAD]}

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)
        self.assertIn("merge_group_failure_reconciled", self._log_types(write_log))

    def test_reconcile_merge_group_queue_enrollment_alone_does_not_mask_failure(self) -> None:
        """Enrollment says the PR is in *a* group, never which one.

        The failing group may be the very group the PR is enrolled in, so
        enrollment can only prompt further checking. With no newer group that
        provably enrolled this head, the failure is genuine and recovery runs.
        """
        run_fixture = self._failure_run(id=31321422749)
        enrolled_node = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={
                "position": 1,
                "state": "AWAITING_CHECKS",
                "enqueuedAt": "2026-09-06T01:24:00Z",
                # The entry's group commit is the failing group itself.
                "headCommit": {"oid": self.GROUP_HEAD_OLD},
                "baseCommit": {"oid": self.QUEUE_BASE_OLD},
            },
        )

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_node=enrolled_node, runs=[run_fixture]),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True) as commit_trans,
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertTrue(changed)
        commit_trans.assert_called_once()
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)
        self.assertEqual(self._log_types(write_log), ["merge_group_failure_reconciled"])
        # The entry's group is the failing group: revoked once, not twice.
        self.assertEqual(
            [t["sha"] for t in self.task["review_gate_revocations"]], [self.GROUP_HEAD_OLD]
        )

    def test_reconcile_merge_group_enrolled_pr_still_stale_when_new_group_proven(self) -> None:
        """Enrollment plus a proven newer group is the path that suppresses."""
        old_failed_run = self._failure_run()
        new_pending_run = self._newer_run()
        runs = [old_failed_run, new_pending_run]
        enrolled_node = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={"position": 1, "state": "AWAITING_CHECKS", "enqueuedAt": "2026-09-06T01:29:54Z"},
        )
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_node=enrolled_node, runs=runs, commits=commits),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        stale_log = next(
            call.args[1]
            for call in write_log.call_args_list
            if call.args[1]["type"] == "merge_group_failure_stale"
        )
        self.assertEqual(stale_log["reason"], "pending_group")
        self.assertTrue(stale_log["in_merge_queue"])

    # ------------------------------------------------------------------
    # Unknown answers never demote and never permanently dedupe
    # ------------------------------------------------------------------

    def test_reconcile_merge_group_pr_facts_unavailable_does_not_demote(self) -> None:
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_node=github_bus.GitHubBusError("api down")),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_facts_A_unavailable")

    def test_reconcile_merge_group_empty_or_invalid_pr_node_does_not_demote(self) -> None:
        run_fixture = self._failure_run(id=31321422749)
        invalid_nodes = (
            None,
            {},
            {"state": ""},
            {"state": "OPEN"},
            {"headRefOid": "8eabc973"},
            # An unrecognised state is unknown, never "closed, therefore stale".
            {"state": "UNKNOWN", "headRefOid": "8eabc9734a000000000000000000000000000000"},
            {"state": "LOCKED", "headRefOid": "8eabc9734a000000000000000000000000000000"},
        )
        for node in invalid_nodes:
            with self.subTest(node=node):
                bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
                status_obj = {"tasks": [dict(self.task)], "handoffs": []}
                with (
                    mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=node)),
                    mock.patch("github_reconciliation.write_activity_log") as write_log,
                    mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
                ):
                    changed = github_reconciliation.reconcile_merge_group_runs(
                        self.config, bus_state, status_obj, "o/r", [run_fixture]
                    )
                self.assertFalse(changed)
                commit_trans.assert_not_called()
                self.assertEqual(status_obj["tasks"][0]["status"], "review_approved")
                self.assertNotIn(
                    "merge_group_run:31321422749",
                    bus_state.get("processed_merge_group_run_ids", []),
                )
                log_entry = write_log.call_args.args[1]
                self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
                self.assertEqual(log_entry["reason"], "pr_facts_A_unavailable")

    def test_reconcile_merge_group_runs_snapshot_unavailable_does_not_demote(self) -> None:
        """An unanswered runs query is not proof that no newer group exists."""
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(runs=github_bus.GitHubBusError("runs api down")),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "merge_group_runs_unavailable")

    def test_reconcile_merge_group_association_unknown_does_not_demote(self) -> None:
        """A candidate group whose commit GitHub will not serve stays an open question."""
        old_failed_run = self._failure_run()
        new_pending_run = self._newer_run()
        runs = [old_failed_run, new_pending_run]

        commits = {self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.APPROVED_HEAD]}
        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs, commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertNotIn("merge_group_run:34003742537", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "group_association_unknown")

    def test_reconcile_merge_group_facts_b_merged_is_stale_and_non_mutating(self) -> None:
        """The PR can merge while the runs and commit calls are in flight.

        Facts A said open on the reviewed head; by the time the association
        work finished the PR had merged. Nothing may be mutated on facts A
        alone.
        """
        run_fixture = self._failure_run(id=31321422749)
        merged_node = dict(
            self.default_pr_node,
            state="MERGED",
            merged=True,
            mergedAt="2026-09-06T01:54:20Z",
        )

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(
                    pr_nodes=[self.default_pr_node, merged_node], runs=[run_fixture]
                ),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check") as emit_gate,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        emit_gate.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertIn("merge_group_run:31321422749", self.bus_state["processed_merge_group_run_ids"])
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_stale")
        self.assertEqual(log_entry["reason"], "already_merged")

    def test_reconcile_merge_group_facts_b_head_drift_does_not_demote(self) -> None:
        """A head that advances mid-reconciliation retains, never demotes."""
        run_fixture = self._failure_run(id=31321422749)
        drifted_node = dict(self.default_pr_node, headRefOid=self.ADVANCED_HEAD)

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(
                    pr_nodes=[self.default_pr_node, drifted_node], runs=[run_fixture]
                ),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_head_drift")

    def test_reconcile_merge_group_facts_b_unavailable_does_not_demote(self) -> None:
        run_fixture = self._failure_run(id=31321422749)

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(
                    pr_nodes=[self.default_pr_node, github_bus.GitHubBusError("api down")],
                    runs=[run_fixture],
                ),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_facts_B_unavailable")

    def test_reconcile_merge_group_target_rerun_pending_does_not_demote(self) -> None:
        """The same run id, re-run and now in progress, is no current failure.

        The candidate scan skips the target's own id, so only re-reading the
        target itself can see this.
        """
        batch_run = self._failure_run(id=31321422749)
        fresh_retry = self._failure_run(id=31321422749, conclusion=None, status="in_progress")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[fresh_retry])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [batch_run]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "target_run_retrying")

    def test_reconcile_merge_group_target_rerun_succeeded_is_stale(self) -> None:
        batch_run = self._failure_run(id=31321422749)
        fresh_success = self._failure_run(id=31321422749, conclusion="success")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[fresh_success])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [batch_run]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertIn("merge_group_run:31321422749", self.bus_state["processed_merge_group_run_ids"])
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_stale")
        self.assertEqual(log_entry["reason"], "target_run_succeeded_on_retry")

    def test_reconcile_merge_group_target_unreadable_does_not_demote(self) -> None:
        """Absent from the fresh snapshot and unreadable by id: unknown."""
        batch_run = self._failure_run(id=31321422749)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [batch_run]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "target_run_unresolved")

    def test_reconcile_merge_group_target_resolved_by_single_run_read(self) -> None:
        """Missing from the list snapshot but readable by id: still a failure."""
        batch_run = self._failure_run(id=31321422749)
        other_run = self._newer_run(id=99999999, head_branch="refs/heads/gh-readonly-queue/dev/pr-999-b41f0c9d")

        def stub(args, **kwargs):
            base = self._gh(runs=[other_run, batch_run])
            path = str(args[1]) if len(args) > 1 and args[:1] == ["api"] else ""
            if "actions/runs?" in path:
                # The list snapshot has rotated the target out.
                return {"total_count": 1, "workflow_runs": [other_run]}
            return base(args, **kwargs)

        with (
            mock.patch("github_bus.gh_json", side_effect=stub),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [batch_run]
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertIn("merge_group_failure_reconciled", self._log_types(write_log))

    def test_reconcile_merge_group_different_pr_head_does_not_mask_genuine_failure(self) -> None:
        run_fixture = self._failure_run(id=31321422749)
        drifted_node = dict(self.default_pr_node, headRefOid=self.ADVANCED_HEAD)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=drifted_node)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_head_mismatch")

    def test_reconcile_merge_group_exact_sha_no_prefix_match(self) -> None:
        """Short SHA / startswith prefix match must be rejected."""
        run_fixture = self._failure_run(id=31321422749)
        short_sha_node = dict(self.default_pr_node, headRefOid="8eabc973")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=short_sha_node)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        self.assertEqual(write_log.call_args.args[1]["reason"], "pr_head_mismatch")

    def test_reconcile_merge_group_missing_approved_head_does_not_demote(self) -> None:
        run_fixture = self._failure_run(id=31321422749)
        self.task.pop("approved_head")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh()),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        self.assertEqual(write_log.call_args.args[1]["reason"], "approved_head_missing")

    # ------------------------------------------------------------------
    # Non-failure and non-approved paths keep their existing behaviour
    # ------------------------------------------------------------------

    def test_reconcile_merge_group_success_has_no_side_effects(self) -> None:
        run_fixture = self._failure_run(id=31321422800, conclusion="success")

        with (
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config,
                self.bus_state,
                self.status,
                "o/r",
                [run_fixture],
            )

        self.assertFalse(changed)
        write_log.assert_not_called()
        commit_trans.assert_not_called()
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertEqual(self.task["status"], "review_approved")
        self.assertIn(
            "merge_group_run:31321422800",
            self.bus_state["processed_merge_group_run_ids"],
        )

    def test_reconcile_merge_group_ambiguous_and_unparseable_produce_no_handoff(self) -> None:
        # 1. Unparseable queue ref
        run_unparseable = {
            "id": 40001,
            "head_branch": "some-random-ref",
            "head_sha": "aaa111",
            "conclusion": "failure",
            "status": "completed",
        }
        # 2. Ambiguous correlation (multiple tasks)
        run_ambiguous = {
            "id": 40002,
            "head_branch": "gh-readonly-queue/dev/pr-900-bbb",
            "head_sha": "bbb222",
            "conclusion": "failure",
            "status": "completed",
        }
        status_ambiguous = {
            "tasks": [
                {"id": "TASK-1", "pr_number": 900},
                {"id": "TASK-2", "pr_number": 900},
            ],
            "handoffs": [],
        }

        with (
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            c1 = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_unparseable]
            )
            c2 = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, status_ambiguous, "o/r", [run_ambiguous]
            )

        self.assertFalse(c1)
        self.assertFalse(c2)
        commit_trans.assert_not_called()
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertEqual(len(status_ambiguous["handoffs"]), 0)

        # Activity logs confirm safe diagnostics recorded without side effects
        log_types = self._log_types(write_log)
        self.assertIn("merge_group_failure_unparseable", log_types)
        self.assertIn("merge_group_failure_ambiguous", log_types)

    def test_reconcile_merge_group_stale_event_produces_no_handoff(self) -> None:
        for non_approved_status in ("done", "cancelled", "superseded", "review", "todo"):
            with self.subTest(status=non_approved_status):
                bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
                run_stale = self._failure_run(id=50001)
                status_obj = {
                    "tasks": [
                        {
                            "id": "ODP-TEST-MG-001",
                            "status": non_approved_status,
                            "owner": "Codex",
                            "reviewer": "Claude2",
                            "pr_number": 756,
                        }
                    ],
                    "handoffs": [],
                }

                with (
                    mock.patch("github_reconciliation.write_activity_log") as write_log,
                    mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
                ):
                    changed = github_reconciliation.reconcile_merge_group_runs(
                        self.config, bus_state, status_obj, "o/r", [run_stale]
                    )

                self.assertFalse(changed)
                commit_trans.assert_not_called()
                self.assertEqual(len(status_obj["handoffs"]), 0)
                self.assertEqual(status_obj["tasks"][0]["status"], non_approved_status)
                self.assertEqual(write_log.call_args.args[1]["type"], "merge_group_failure_stale")
                self.assertIn("merge_group_run:50001", bus_state["processed_merge_group_run_ids"])

    def test_reconcile_merge_group_stale_in_progress_preserves_task_and_handoffs(self) -> None:
        """A task reopened to in_progress must not be force-transitioned or have handoffs wiped."""
        run_stale = self._failure_run(id=50002)
        existing_handoff = {
            "task_id": "ODP-TEST-MG-001",
            "from": "Claude2",
            "to": "Codex",
            "message": "rework needed after review",
            "status": "pending",
            "created_at": "2026-09-04T10:00:00Z",
        }
        status_in_progress = {
            "tasks": [
                {
                    "id": "ODP-TEST-MG-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Claude2",
                    "pr_number": 756,
                    "next": "implementing reviewer feedback",
                }
            ],
            "handoffs": [existing_handoff],
        }

        with (
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, status_in_progress, "o/r", [run_stale]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(write_log.call_args.args[1]["type"], "merge_group_failure_stale")
        # Task remains in_progress
        self.assertEqual(status_in_progress["tasks"][0]["status"], "in_progress")
        self.assertEqual(status_in_progress["tasks"][0]["next"], "implementing reviewer feedback")
        # Existing reviewer->owner handoff must remain pending and untouched
        self.assertEqual(len(status_in_progress["handoffs"]), 1)
        self.assertEqual(status_in_progress["handoffs"][0]["status"], "pending")
        self.assertEqual(status_in_progress["handoffs"][0]["from"], "Claude2")
        self.assertEqual(status_in_progress["handoffs"][0]["to"], "Codex")
        self.assertIn("merge_group_run:50002", self.bus_state["processed_merge_group_run_ids"])

    def test_reconcile_merge_group_stale_blocked_preserves_task_and_handoffs(self) -> None:
        """A blocked task must not be mutated by stale merge_group failure runs."""
        run_stale = self._failure_run(id=50003)
        existing_handoff = {
            "task_id": "ODP-TEST-MG-001",
            "from": "Codex",
            "to": "Human/Ops",
            "message": "waiting for environment access",
            "status": "pending",
            "created_at": "2026-09-04T10:00:00Z",
        }
        status_blocked = {
            "tasks": [
                {
                    "id": "ODP-TEST-MG-001",
                    "status": "blocked",
                    "owner": "Codex",
                    "reviewer": "Claude2",
                    "pr_number": 756,
                    "next": "waiting for unblock",
                }
            ],
            "handoffs": [existing_handoff],
        }

        with (
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, status_blocked, "o/r", [run_stale]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(write_log.call_args.args[1]["type"], "merge_group_failure_stale")
        self.assertEqual(status_blocked["tasks"][0]["status"], "blocked")
        self.assertEqual(len(status_blocked["handoffs"]), 1)
        self.assertEqual(status_blocked["handoffs"][0]["status"], "pending")
        self.assertIn("merge_group_run:50003", self.bus_state["processed_merge_group_run_ids"])

    def test_reconcile_merge_group_in_progress_waits_for_completion(self) -> None:
        run_in_progress = self._newer_run(id=60001)

        with (
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_in_progress]
            )

        self.assertFalse(changed)
        write_log.assert_not_called()
        commit_trans.assert_not_called()
        self.assertNotIn("merge_group_run:60001", self.bus_state["processed_merge_group_run_ids"])

    def test_classify_fresh_target_run_status_and_conclusion_permutations(self) -> None:
        classify = github_reconciliation.classify_fresh_target_run
        # Legal completed failure
        res, obj = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "failure"}])
        self.assertEqual(res, github_reconciliation.TARGET_CURRENT_FAILURE)
        self.assertEqual(obj["id"], 1)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "cancelled"}])
        self.assertEqual(res, github_reconciliation.TARGET_CURRENT_FAILURE)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "timed_out"}])
        self.assertEqual(res, github_reconciliation.TARGET_CURRENT_FAILURE)

        # Legal completed success
        res, obj = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "success"}])
        self.assertEqual(res, github_reconciliation.TARGET_RETRY_SUCCEEDED)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "neutral"}])
        self.assertEqual(res, github_reconciliation.TARGET_RETRY_SUCCEEDED)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "skipped"}])
        self.assertEqual(res, github_reconciliation.TARGET_RETRY_SUCCEEDED)

        # Completed with unrecognised conclusion -> unknown
        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": "unrecognised_conclusion"}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "completed", "conclusion": ""}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        # Normal pending retrying without conclusion
        for pending_status in ("in_progress", "queued", "waiting", "requested", "pending"):
            res, _ = classify("o/r", 1, [{"id": 1, "status": pending_status, "conclusion": ""}])
            self.assertEqual(res, github_reconciliation.TARGET_RETRYING, pending_status)

        # Contradictory payloads: pending status with conclusion -> unknown
        res, _ = classify("o/r", 1, [{"id": 1, "status": "in_progress", "conclusion": "failure"}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "in_progress", "conclusion": "success"}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        # Missing or unrecognised status -> unknown
        res, _ = classify("o/r", 1, [{"id": 1, "status": "", "conclusion": "failure"}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        res, _ = classify("o/r", 1, [{"id": 1, "status": "", "conclusion": "success"}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        res, _ = classify("o/r", 1, [{"id": 1, "status": None, "conclusion": None}])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

        res, _ = classify("o/r", 1, [])
        self.assertEqual(res, github_reconciliation.TARGET_UNKNOWN)

    def test_pr_queue_identity_helper(self) -> None:
        queue_id = github_reconciliation.pr_queue_identity
        # Not enrolled
        self.assertEqual(queue_id(None), (False, ""))
        self.assertEqual(queue_id({}), (False, ""))
        self.assertEqual(queue_id({"isInMergeQueue": False}), (False, ""))

        # Enrolled with mergeQueueEntry
        node1 = {
            "isInMergeQueue": True,
            "mergeQueueEntry": {"position": 1, "enqueuedAt": "2026-09-06T01:00:00Z"},
        }
        self.assertEqual(queue_id(node1), (True, "2026-09-06T01:00:00Z"))

        # Position progression does not change identity
        node2 = {
            "isInMergeQueue": True,
            "mergeQueueEntry": {"position": 3, "enqueuedAt": "2026-09-06T01:00:00Z"},
        }
        self.assertEqual(queue_id(node1), queue_id(node2))

        # Re-enqueued with new timestamp changes identity
        node3 = {
            "isInMergeQueue": True,
            "mergeQueueEntry": {"position": 1, "enqueuedAt": "2026-09-06T01:30:00Z"},
        }
        self.assertNotEqual(queue_id(node1), queue_id(node3))

    def test_reconcile_merge_group_delayed_failure_for_previous_head_is_stale_and_non_mutating(self) -> None:
        """A delayed failure for an earlier PR head A does not revoke approval on head B."""
        old_head_failure_run = self._failure_run(id=34003742537)
        commits = {
            self.GROUP_HEAD_OLD: [self.QUEUE_BASE_OLD, self.ADVANCED_HEAD],
        }

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[old_head_failure_run], commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [old_head_failure_run]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertEqual(self.task["approved_head"], self.APPROVED_HEAD)
        self.assertEqual(len(self.status["handoffs"]), 0)
        self.assertIn("merge_group_run:34003742537", self.bus_state["processed_merge_group_run_ids"])
        stale_log = write_log.call_args.args[1]
        self.assertEqual(stale_log["type"], "merge_group_failure_stale")
        self.assertEqual(stale_log["reason"], "target_not_for_approved_head")

    def test_reconcile_merge_group_target_parents_api_unknown_does_not_demote(self) -> None:
        """Target run whose parents GitHub cannot answer is unknown, not demoted."""
        run_fixture = self._failure_run(id=34003742537)
        commits = {self.GROUP_HEAD_OLD: github_bus.GitHubBusError("commit parents unavailable")}

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture], commits=commits)),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:34003742537", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "target_association_unknown")

    def test_reconcile_merge_group_target_missing_head_sha_is_unknown_does_not_demote(self) -> None:
        """Target run with no head SHA cannot prove association: unknown, not demoted."""
        run_fixture = self._failure_run(id=34003742537, head_sha="")

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:34003742537", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "target_identity_incomplete")

    def test_reconcile_merge_group_facts_b_queue_generation_drift_does_not_demote(self) -> None:
        """Re-enqueuing on the same head between Facts A and B is queue drift: retain."""
        run_fixture = self._failure_run(id=31321422749)
        node_a = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={"position": 1, "enqueuedAt": "2026-09-06T01:00:00Z"},
        )
        node_b = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={"position": 1, "enqueuedAt": "2026-09-06T01:30:00Z"},
        )

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_nodes=[node_a, node_b], runs=[run_fixture]),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_queue_drift")

    def test_reconcile_merge_group_facts_b_newly_enrolled_drift_does_not_demote(self) -> None:
        """PR newly entering the queue between Facts A and B is queue drift: retain."""
        run_fixture = self._failure_run(id=31321422749)
        node_a = dict(self.default_pr_node, isInMergeQueue=False, mergeQueueEntry=None)
        node_b = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={"position": 1, "enqueuedAt": "2026-09-06T01:30:00Z"},
        )

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_nodes=[node_a, node_b], runs=[run_fixture]),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertFalse(changed)
        commit_trans.assert_not_called()
        self.assertEqual(self.task["status"], "review_approved")
        self.assertNotIn("merge_group_run:31321422749", self.bus_state.get("processed_merge_group_run_ids", []))
        log_entry = write_log.call_args.args[1]
        self.assertEqual(log_entry["type"], "merge_group_failure_unresolved")
        self.assertEqual(log_entry["reason"], "pr_queue_drift")

    def test_reconcile_merge_group_facts_b_position_movement_alone_is_not_drift(self) -> None:
        """Position moving from 3 to 1 with the same enqueuedAt is not drift."""
        run_fixture = self._failure_run(id=31321422749)
        node_a = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            mergeQueueEntry={"position": 3, "enqueuedAt": "2026-09-06T01:00:00Z", "headCommit": None},
        )
        node_b = dict(
            self.default_pr_node,
            isInMergeQueue=True,
            # GitHub has not built a group commit for the entry yet.
            mergeQueueEntry={"position": 1, "enqueuedAt": "2026-09-06T01:00:00Z", "headCommit": None},
        )

        with (
            mock.patch(
                "github_bus.gh_json",
                side_effect=self._gh(pr_nodes=[node_a, node_b], runs=[run_fixture]),
            ),
            mock.patch("github_reconciliation.write_activity_log") as write_log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", [run_fixture]
            )

        self.assertTrue(changed)
        self.assertEqual(self.task["status"], "review")
        self.assertEqual(len(self.status["handoffs"]), 1)
        self.assertIn("merge_group_failure_reconciled", self._log_types(write_log))

    # ------------------------------------------------------------------
    # Recovery -> canonical CAS/reload -> ready dispatcher, end to end
    # ------------------------------------------------------------------

    ROUTED_AT = "2026-09-06T01:20:00Z"

    def _route_approved_head(self) -> dict:
        """The admission record the single merge dispatcher left for this head."""
        route = {
            "head": self.APPROVED_HEAD,
            "route": "queued",
            "pr_number": 756,
            "at": self.ROUTED_AT,
            "attempts": 2,
        }
        self.task["merge_route"] = dict(route)
        return route

    def _canonical_store(self) -> Path:
        """Back the board with a real canonical status file and CAS boundary."""
        self._bind_real_source_range()
        tmp = tempfile.TemporaryDirectory(prefix="test-mg-canonical-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        status_path = root / "ai-status.json"
        self.config.setdefault("paths", {}).update(
            {
                "status_file": str(status_path),
                "activity_log": str(root / "ai-activity-log.jsonl"),
                "event_queue": str(root / "event-queue.jsonl"),
            }
        )
        self.config.update(
            {
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                    "handoffs_path": "handoffs",
                },
                "ready_dispatcher": {
                    "enabled": True,
                    "review_statuses": ["review"],
                    "owned_statuses": ["in_progress", "todo"],
                    "active_worker_statuses": ["running"],
                    "max_dispatches_per_tick": 4,
                },
                # The legacy watcher stays off: the ready dispatcher is the one
                # producer of review work.
                "events": {"enqueue_runtime_events": False},
                "providers": {},
            }
        )
        self.task["review_gate_sha"] = self.APPROVED_HEAD
        self.task["review_gate_target"] = {
            "repo_slug": "o/r", "sha": self.APPROVED_HEAD, "context": "task-review-gate",
        }
        status_path.write_text(json.dumps(self.status), encoding="utf-8")
        # The canonical status launcher the Supervisor resolves under the
        # status root. It is never executed: the process transport below
        # serves it with the real `ai_status.main` against this board.
        launcher = root / "scripts" / status_transition.STATUS_LAUNCHER_NAME
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        launcher.chmod(0o755)
        transport = mock.patch.object(subprocess, "run", side_effect=self._process_transport(status_path))
        transport.start()
        self.addCleanup(transport.stop)
        return status_path

    # ai_status / task_archive paths that resolve to the status root.
    STATUS_ROOT_PATHS = {
        "STATUS_ROOT": "",
        "STATUS_FILE": "ai-status.json",
        "LOG_FILE": "ai-activity-log.jsonl",
        "CURRENT_WORK_FILE": "current-work.md",
        "DOCS_SITE_DIR": "docs-site",
        "CONFIG_FILE": ".orchestrator/config.json",
        "STATUS_ROOT_CONFIG_LOCAL_FILE": ".orchestrator/config.local.json",
        "PLANNING_STATE_FILE": ".orchestrator/planning-state.json",
        "ORCHESTRATOR_STATE_FILE": ".orchestrator/state.json",
        "APPROVAL_QUEUE_FILE": ".orchestrator/approval-queue.json",
        "DASHBOARD_BUNDLE_FILE": "dashboard-bundle.json",
        "ARCHIVE_TASKS_DIR": "ai-task-archive/tasks",
    }
    ARCHIVE_ROOT_PATHS = {
        "STATUS_ROOT": "",
        "ARCHIVE_DIR": "ai-task-archive",
        "ARCHIVE_TASKS_DIR": "ai-task-archive/tasks",
        "ARCHIVE_INDEX_FILE": "ai-task-archive/index.json",
    }

    def _ai_status_cli(self, status_path: Path, argv: list[str], *, actor: str | None) -> list[dict]:
        """One real `ai_status` CLI run (`main`) against the temporary board.

        `main` is the whole command: load under the status lock, the command,
        `sync_all`, the outbox/status-check emission and the second `sync_all`.
        Every status-root path of `ai_status`/`task_archive` points at the
        temporary root for the call and is restored after it; nothing here
        can reach the live board. A refused command raises `SystemExit` as
        the CLI does. Returns the activity-log entries the call appended.
        """
        import task_archive

        root = status_path.parent
        log_path = root / "ai-activity-log.jsonl"
        before = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        # The committed example roster is the config fixture, as in
        # scripts/test_ai_status.py: the temporary root has no config.json,
        # and a worker's inherited runtime config must never be read here.
        example_config = str(Path(__file__).resolve().parent / "config.example.json")
        environ = {
            "PANTHEON_STATUS_ROOT": str(root),
            "ORCH_STATUS_ROOT": str(root),
            "ORCH_CONFIG_PATH": example_config,
            "PANTHEON_CONFIG_PATH": example_config,
        }
        if actor:
            environ["AI_NAME"] = actor
        with contextlib.ExitStack() as stack:
            for name, relative in self.STATUS_ROOT_PATHS.items():
                stack.enter_context(mock.patch.object(ai_status, name, root / relative if relative else root))
            for name, relative in self.ARCHIVE_ROOT_PATHS.items():
                stack.enter_context(mock.patch.object(task_archive, name, root / relative if relative else root))
            stack.enter_context(mock.patch.dict(os.environ, environ, clear=False))
            if not actor:
                os.environ.pop("AI_NAME", None)  # restored by the patch above
            self.assertEqual(ai_status.STATUS_FILE, status_path)
            ai_status.main(["ai_status.py", *argv])
        after = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        return [json.loads(line) for line in after[len(before):] if line.strip()]

    def _process_transport(self, status_path: Path):
        """`subprocess.run` for this test: the launcher, no GitHub, real Git.

        The Supervisor's original launcher calls (`sync_status_pipeline`,
        `sync_dispatched_task_status`) reach the real `ai_status.main` for the
        temporary board, with the actor the launcher env names. Any `gh`
        process fails closed -- no live GitHub. Everything else (Git on the
        synthetic source repository) runs for real.
        """
        real_run = subprocess.run
        launcher = str(status_path.parent / "scripts" / status_transition.STATUS_LAUNCHER_NAME)

        def run(argv, *args, **kwargs):
            command = [str(part) for part in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
            if command and command[0] == launcher:
                env = kwargs.get("env") or {}
                self.assertEqual(env.get("PANTHEON_STATUS_ROOT"), str(status_path.parent))
                self.assertEqual(env.get("ORCH_STATUS_ROOT"), str(status_path.parent))
                out, err = io.StringIO(), io.StringIO()
                code = 0
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        self._ai_status_cli(status_path, command[1:], actor=env.get("AI_NAME"))
                    except SystemExit as exc:
                        code = exc.code if isinstance(exc.code, int) else 1
                        if not isinstance(exc.code, int):
                            err.write(str(exc.code))
                self.launcher_calls.append((command[1:], env.get("AI_NAME"), code))
                return subprocess.CompletedProcess(command, code, out.getvalue(), err.getvalue())
            if command and Path(command[0]).name == "gh":
                return subprocess.CompletedProcess(command, 1, "", "no live GitHub in this test")
            return real_run(argv, *args, **kwargs)

        self.launcher_calls: list = []
        return run

    def _canonical_sync(self, status_path: Path):
        """The `ai-status.sh sync` the canonical commit runs: the real CLI `sync`.

        This stands in for `supervisor.sync_status_pipeline` only where a test
        wants to count or inspect those calls; it runs exactly what the
        original pipeline's launcher runs, through the same process transport.
        """
        launcher = str(status_path.parent / "scripts" / status_transition.STATUS_LAUNCHER_NAME)

        def _sync(config) -> bool:
            result = subprocess.run(
                [launcher, "sync"],
                cwd=str(status_path.parent),
                capture_output=True,
                text=True,
                env=status_transition._status_launcher_env(config),
            )
            return result.returncode == 0

        return _sync

    def _recover(self, status_path: Path, *, gh, post, extra=()):
        """One bus poll: load canonical, reconcile, reload -- as sync_github_bus does."""
        import supervisor

        status = supervisor.load_status(self.config)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("github_bus.gh_json", side_effect=gh))
            stack.enter_context(mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._canonical_sync(status_path)))
            stack.enter_context(mock.patch.object(ai_status, "post_task_review_status_payload", side_effect=post))
            stack.enter_context(mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD))
            stack.enter_context(mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"))
            for patcher in extra:
                stack.enter_context(patcher)
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, status, "o/r", list(self._runs)
            )
        return changed, supervisor.load_status(self.config)

    def _pr_transport(self, github_pr, calls: list):
        """`gh pr list/view --json` for the one PR GitHub would answer about.

        Only the CLI transport is replaced: the provenance reader, its exact
        selection and the repository resolution are the real ones. Each call
        is recorded, so a test can tell a fresh read from a cached answer.
        `github_pr` is a node, a callable returning one per call, or None for
        GitHub not answering.
        """

        def answer():
            return github_pr() if callable(github_pr) else github_pr

        def view(args, *, cwd=None):
            calls.append(("view", list(args)))
            node = answer()
            return dict(node) if isinstance(node, dict) else None

        def listing(args, *, cwd=None):
            calls.append(("list", list(args)))
            node = answer()
            return [dict(node)] if isinstance(node, dict) else []

        return view, listing

    def _ready_dispatch(
        self, queued: list, *, pending_events=(), pr_status=("OPEN", "success"), state=None,
        github_pr=None, gh_calls=None,
    ):
        """The original ready producer, on the canonical store, events disabled."""
        import runtime_state
        import supervisor

        state = runtime_state.default_state() if state is None else state
        offline = github_bus.GitHubBusOffline("no live GitHub in this test")
        gh_view, gh_list = self._pr_transport(github_pr, [] if gh_calls is None else gh_calls)
        source_repo = self._source_repo() if "coordination" in self.config else None
        patches = (
            # Nothing in this chain may reach the real GitHub; every remote read
            # the dispatcher would make either is stubbed below or fails closed.
            mock.patch("github_bus.gh_json", side_effect=offline),
            mock.patch("github_bus.run_gh", side_effect=offline),
            mock.patch.object(dispatch_engine, "task_reality_reconcile_is_due", return_value=False),
            # `sync_status_pipeline` is the original one: its launcher call is
            # served by the real CLI `sync` through the process transport.
            mock.patch.object(ai_status, "post_task_review_status_payload", return_value=(True, "")),
            mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
            mock.patch.object(supervisor, "load_event_queue", return_value=list(pending_events)),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=(
                    {"claude2" for _ in pending_events},
                    {(e["task_id"], "claude2") for e in pending_events},
                    {e["key"] for e in pending_events},
                ),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
            mock.patch.object(ai_status, "task_pr_ci_status", return_value=pr_status),
            # The PR provenance reader's transport and repository binding.
            mock.patch.object(ai_status, "run_gh_json_command", side_effect=gh_view),
            mock.patch.object(ai_status, "run_gh_json_list_command", side_effect=gh_list),
            mock.patch.object(ai_status, "status_runtime_config", return_value={}),
            mock.patch.object(ai_status, "task_repository_id", return_value="pantheon"),
            mock.patch.object(ai_status, "repository_local_path", return_value=source_repo),
            mock.patch.object(ai_status, "repository_slug", return_value="o/r"),
            mock.patch.object(ai_status, "delivery_merge_target_branch", return_value="dev"),
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                side_effect=lambda _config, event: queued.append(event) or True,
            ),
        )
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            dispatch_engine.dispatch_ready_tasks(self.config, state, provider_report={})
            if queued:
                # The queue consumer re-derives the key from the canonical board
                # before launching; a recovery it cannot re-prove is skipped.
                task_map = dispatch_engine.task_index_from_status(
                    self.config, supervisor.load_status(self.config)
                )
                return [
                    dispatch_engine.stale_dispatch_skip_message(self.config, event, task_map)
                    for event in queued
                ]
        return []

    def _active_group_node(self, group_head):
        return dict(
            self.default_pr_node,
            headRefOid=self.APPROVED_HEAD,
            isInMergeQueue=True,
            mergeQueueEntry={
                "position": 1,
                "state": "AWAITING_CHECKS",
                "enqueuedAt": "2026-09-06T01:26:00Z",
                "headCommit": {"oid": group_head},
                "baseCommit": {"oid": self.QUEUE_BASE_NEW},
            },
        )

    def test_recovery_chain_revokes_gates_and_dispatches_reviewer_once_with_route_kept(self) -> None:
        status_path = self._canonical_store()
        route = self._route_approved_head()
        status_path.write_text(json.dumps(self.status), encoding="utf-8")
        self._runs = [self._failure_run(id=31321422749)]
        commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}
        posts: list[dict] = []

        changed, canonical = self._recover(
            status_path,
            gh=self._gh(pr_node=self._active_group_node(self.GROUP_HEAD_NEW), runs=self._runs, commits=commits),
            post=lambda payload: posts.append(dict(payload)) or (True, ""),
        )

        self.assertTrue(changed)
        task = canonical["tasks"][0]
        self.assertEqual(task["status"], "review")
        self.assertNotIn("approved_head", task)
        # The route is the admission record; recovery neither resets nor bumps it.
        self.assertEqual(task["merge_route"], route)
        # Pending on the PR head and on both group commits that enrolled it:
        # the failing group and the PR's current queue group.
        self.assertEqual(
            sorted((p["sha"], p["state"]) for p in posts),
            sorted([
                (self.APPROVED_HEAD, "pending"),
                (self.GROUP_HEAD_OLD, "pending"),
                (self.GROUP_HEAD_NEW, "pending"),
            ]),
        )
        self.assertTrue(all(p["repo_slug"] == "o/r" and p["context"] == "task-review-gate" for p in posts))
        # Delivered and persisted by the canonical sync: nothing left to retry,
        # and the reload kept the result instead of discarding it.
        self.assertNotIn("status_check_outbox", task)
        self.assertNotIn("review_gate_revocations", task)
        self.assertNotIn("review_gate_refresh_pending", task)
        self.assertEqual(task["review_gate_sha"], self.APPROVED_HEAD)
        self.assertIn("merge_group_run:31321422749", self.bus_state["processed_merge_group_run_ids"])

        queued: list[dict] = []
        skips = self._ready_dispatch(queued)
        self.assertEqual([(e["task_id"], e["reason"], e["target_agent"]) for e in queued],
                         [("ODP-TEST-MG-001", "review_ready_dispatch", "Claude2")])
        self.assertEqual(skips, [None])
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]["merge_route"], route)

        # The same cause with its event already pending is not dispatched again.
        again: list[dict] = []
        self._ready_dispatch(again, pending_events=queued)
        self.assertEqual(again, [])

        # A second poll of the same failure is a no-op: no POST, no new handoff.
        posts.clear()
        changed, canonical = self._recover(
            status_path,
            gh=self._gh(pr_node=self._active_group_node(self.GROUP_HEAD_NEW), runs=self._runs, commits=commits),
            post=lambda payload: posts.append(dict(payload)) or (True, ""),
        )
        self.assertFalse(changed)
        self.assertEqual(posts, [])
        self.assertEqual(len([h for h in canonical["handoffs"] if h["status"] == "pending"]), 1)

    def test_recovery_chain_cas_reject_has_no_side_effects(self) -> None:
        status_path = self._canonical_store()
        self._route_approved_head()
        status_path.write_text(json.dumps({**self.status, "_status_write_revision": "r1"}), encoding="utf-8")
        self._runs = [self._failure_run(id=31321422749)]
        import supervisor

        stale = supervisor.load_status(self.config)
        # Another canonical writer advances the revision mid-reconciliation.
        newer = json.loads(status_path.read_text(encoding="utf-8"))
        newer["_status_write_revision"] = "r2"
        newer["tasks"][0]["next"] = "newer canonical write"
        status_path.write_text(json.dumps(newer), encoding="utf-8")
        post = mock.Mock(return_value=(True, ""))
        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=self._runs)),
            mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._canonical_sync(status_path)) as sync,
            mock.patch.object(ai_status, "post_task_review_status_payload", post),
        ):
            changed = github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, stale, "o/r", list(self._runs)
            )

        self.assertFalse(changed)
        sync.assert_not_called()
        post.assert_not_called()
        self.assertNotIn("merge_group_run:31321422749", self.bus_state["processed_merge_group_run_ids"])
        on_disk = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["tasks"][0]["status"], "review_approved")
        self.assertEqual(on_disk["handoffs"], [])
        queued: list[dict] = []
        self._ready_dispatch(queued)
        self.assertEqual(queued, [])

    def test_recovery_chain_rejected_or_unacknowledged_post_survives_reload(self) -> None:
        for label, error in (
            ("rejected", "Failed to emit status check (code 1): HTTP 422"),
            ("ack_unknown", "Error during status emission: timed out"),
        ):
            with self.subTest(label):
                self.setUp()
                status_path = self._canonical_store()
                self._route_approved_head()
                status_path.write_text(json.dumps(self.status), encoding="utf-8")
                self._runs = [self._failure_run(id=31321422749)]
                commits = {self.GROUP_HEAD_NEW: self._group_enrolling_approved_head()}
                gh = self._gh(pr_node=self._active_group_node(self.GROUP_HEAD_NEW), runs=self._runs, commits=commits)

                changed, canonical = self._recover(status_path, gh=gh, post=lambda _p, _error=error: (False, _error))

                self.assertTrue(changed)
                task = canonical["tasks"][0]
                # Every revocation and the fresh pending payload are retained
                # with their real error after the reload; none is read as sent.
                outbox = {(p["sha"], p["state"]): p for p in task["status_check_outbox"]}
                self.assertEqual(
                    sorted(outbox),
                    sorted([
                        (self.APPROVED_HEAD, "pending"),
                        (self.GROUP_HEAD_OLD, "pending"),
                        (self.GROUP_HEAD_NEW, "pending"),
                    ]),
                )
                self.assertTrue(all(p["last_error"] == error for p in outbox.values()))
                self.assertTrue(task["review_gate_refresh_pending"])

                # A later canonical sync drains the same exact payloads.
                delivered = mock.Mock(return_value=(True, ""))
                with (
                    mock.patch.object(ai_status, "post_task_review_status_payload", delivered),
                    mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
                    mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
                ):
                    self._canonical_sync(status_path)(self.config)
                drained = json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]
                self.assertNotIn("status_check_outbox", drained)
                self.assertNotIn("review_gate_refresh_pending", drained)
                sent = [c.args[0] for c in delivered.call_args_list]
                self.assertTrue({self.APPROVED_HEAD, self.GROUP_HEAD_OLD, self.GROUP_HEAD_NEW}
                                <= {p["sha"] for p in sent})
                self.assertTrue(all(p["state"] == "pending" for p in sent))

    def test_recovery_active_group_not_enrolling_head_is_not_revoked(self) -> None:
        status_path = self._canonical_store()
        self._route_approved_head()
        status_path.write_text(json.dumps(self.status), encoding="utf-8")
        self._runs = [self._failure_run(id=31321422749)]
        # The current queue group is built for another PR only.
        commits = {self.GROUP_HEAD_NEW: [self.QUEUE_BASE_NEW, self.ADVANCED_HEAD]}
        posts: list[dict] = []
        changed, _ = self._recover(
            status_path,
            gh=self._gh(pr_node=self._active_group_node(self.GROUP_HEAD_NEW), runs=self._runs, commits=commits),
            post=lambda payload: posts.append(dict(payload)) or (True, ""),
        )
        self.assertTrue(changed)
        self.assertNotIn(self.GROUP_HEAD_NEW, {p["sha"] for p in posts})
        self.assertEqual({p["sha"] for p in posts}, {self.APPROVED_HEAD, self.GROUP_HEAD_OLD})

    def test_recovery_active_group_unknown_identity_or_association_does_not_demote(self) -> None:
        unknown_entry = dict(self._active_group_node(self.GROUP_HEAD_NEW))
        unknown_entry["mergeQueueEntry"] = {"position": 1, "enqueuedAt": "2026-09-06T01:26:00Z"}
        def without(*fields):
            return {k: v for k, v in self.default_pr_node.items() if k not in fields}

        cases = (
            ("active_group_identity_unknown", unknown_entry, {}),
            ("active_group_identity_unknown",
             dict(self.default_pr_node, isInMergeQueue=True, mergeQueueEntry=None), {}),
            # The transport answered state and head but not the queue: a node
            # missing either queue field, or with a non-boolean flag, is not
            # GitHub saying "not enrolled", so no target list is derived from it.
            ("active_group_identity_unknown", without("mergeQueueEntry"), {}),
            ("active_group_identity_unknown", without("isInMergeQueue"), {}),
            ("active_group_identity_unknown", without("isInMergeQueue", "mergeQueueEntry"), {}),
            ("active_group_identity_unknown", dict(self.default_pr_node, isInMergeQueue=None), {}),
            ("active_group_identity_unknown", dict(self.default_pr_node, isInMergeQueue="false"), {}),
            # An entry beside a flag that is not true contradicts itself.
            ("active_group_identity_unknown",
             dict(self._active_group_node(self.GROUP_HEAD_NEW), isInMergeQueue=False), {}),
            # The group commit's parents cannot be read.
            ("active_group_association_unknown", self._active_group_node(self.GROUP_HEAD_NEW), None),
        )
        for reason, node, commits in cases:
            with self.subTest(reason=reason, node=node):
                self.setUp()
                run_fixture = self._failure_run(id=31321422749)
                with (
                    mock.patch("github_bus.gh_json", side_effect=self._gh(pr_node=node, runs=[run_fixture], commits=commits)),
                    mock.patch("github_reconciliation.write_activity_log") as write_log,
                    mock.patch("status_transition.commit_canonical_task_transition") as commit_trans,
                    mock.patch.object(ai_status, "post_task_review_status_payload") as post,
                ):
                    changed = github_reconciliation.reconcile_merge_group_runs(
                        self.config, self.bus_state, self.status, "o/r", [run_fixture]
                    )
                self.assertFalse(changed)
                commit_trans.assert_not_called()
                post.assert_not_called()
                self.assertEqual(self.task["status"], "review_approved")
                self.assertNotIn("merge_group_run:31321422749", self.bus_state["processed_merge_group_run_ids"])
                self.assertIn(reason, [c.args[1].get("reason") for c in write_log.call_args_list])

    def test_route_recovery_eligibility_requires_exact_committed_cause(self) -> None:
        """Only the committed cause for exactly this task/PR/head/reviewer lifts the route."""
        import supervisor

        status_path = self._canonical_store()
        route = self._route_approved_head()
        self.task.update({"status": "review", "next": "Merge group run 1 failed; reviewer recovery handoff dispatched"})
        self.task.pop("approved_head")
        cause = {
            "task_id": "ODP-TEST-MG-001",
            "from": "Codex",
            "to": "Claude2",
            "message": "Merge group failed for PR #756; fresh review required.",
            "status": "pending",
            "created_at": "2026-09-06T01:30:00Z",
            "reason": "merge_group_failure",
            "run_id": 31321422749,
            "queue_ref": "refs/heads/gh-readonly-queue/dev/pr-756-a297b2a0",
            "head_sha": self.GROUP_HEAD_OLD,
            "pr_number": 756,
            "source_head": self.APPROVED_HEAD,
        }

        def eligible(handoffs, *, task_overrides=None, snapshot_overrides=None, agent="Claude2"):
            board = json.loads(json.dumps(self.status))
            board["tasks"][0].update(task_overrides or {})
            board["handoffs"] = handoffs
            status_path.write_text(json.dumps(board), encoding="utf-8")
            snapshot = supervisor.load_status(self.config)["tasks"][0]
            snapshot.update(snapshot_overrides or {})
            with (
                mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
                mock.patch.object(ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
            ):
                return dispatch_engine.is_task_review_dispatch_eligible(self.config, snapshot, agent)

        self.assertTrue(eligible([cause]))
        rejected = {
            "no committed cause (next text only)": ([], {}, {}),
            "legacy cause without source head": ([{k: v for k, v in cause.items() if k != "source_head"}], {}, {}),
            "cause for another head": ([dict(cause, source_head=self.ADVANCED_HEAD)], {}, {}),
            "cause for another PR": ([dict(cause, pr_number=757)], {}, {}),
            "queue ref for another PR": ([dict(cause, queue_ref="refs/heads/gh-readonly-queue/dev/pr-757-a297b2a0")], {}, {}),
            "group head equals PR head": ([dict(cause, head_sha=self.APPROVED_HEAD)], {}, {}),
            "missing run id": ([dict(cause, run_id=None)], {}, {}),
            "cause to another reviewer": ([dict(cause, to="Codex2")], {}, {}),
            "cause older than the route": ([dict(cause, created_at="2026-09-06T01:00:00Z")], {}, {}),
            "cause without timestamp": ([dict(cause, created_at=None)], {}, {}),
            "resolved cause": ([dict(cause, status="done")], {}, {}),
            "other reason": ([dict(cause, reason="manual")], {}, {}),
            "superseded by a later pending handoff": (
                [cause, {"task_id": "ODP-TEST-MG-001", "to": "Codex", "status": "pending",
                         "created_at": "2026-09-06T02:00:00Z", "reason": "manual"}], {}, {}),
            "route for another PR": ([cause], {"merge_route": dict(route, pr_number=757)}, {}),
            "route without timestamp": ([cause], {"merge_route": {k: v for k, v in route.items() if k != "at"}}, {}),
            "snapshot not the committed task": ([cause], {}, {"review_submission": dict(self.task["review_submission"], pr_number=757)}),
        }
        for label, (handoffs, task_overrides, snapshot_overrides) in rejected.items():
            with self.subTest(label):
                self.assertFalse(eligible(handoffs, task_overrides=task_overrides, snapshot_overrides=snapshot_overrides))

        # The route itself is never rewritten by the eligibility read.
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]["merge_route"], route)

        # Self-review and source-author review stay refused with a valid cause.
        self.assertFalse(eligible([dict(cause, to="Codex")], task_overrides={"reviewer": "Codex"}, agent="Codex"))
        with (
            mock.patch.object(dispatch_engine, "review_source_block_reason", return_value="reviewer authored source"),
            mock.patch.object(supervisor, "review_source_block_reason", return_value="reviewer authored source"),
        ):
            self.assertFalse(eligible([cause]))
        # A merged PR whose submission was not verified as merged recovery stays refused.
        with (
            mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
            mock.patch.object(ai_status, "task_pr_ci_status", return_value=("MERGED", "success")),
        ):
            board = json.loads(json.dumps(self.status))
            board["handoffs"] = [cause]
            status_path.write_text(json.dumps(board), encoding="utf-8")
            self.assertFalse(dispatch_engine.is_task_review_dispatch_eligible(
                self.config, supervisor.load_status(self.config)["tasks"][0], "Claude2"))

    def test_route_recovery_refuses_before_reading_another_prs_ci(self) -> None:
        """The task's own PR is part of the recovery identity, read before any CI.

        The dispatcher's CI read resolves the task's top-level `pr_number`
        (`task_pr_lookup_scope`), not the submission's. A canonical task whose
        top-level PR drifted, is missing or malformed -- or a caller snapshot
        that names another PR than the committed task -- must be refused by
        the recovery identity before GitHub is asked about any PR, even when
        that other PR is green. Only the real CI reader runs here; its `gh`
        transport is the one thing replaced, and every PR it is asked about
        is recorded.
        """
        import supervisor

        status_path = self._canonical_store()
        self._route_approved_head()
        self.task.update({"status": "review", "next": "Merge group run 1 failed; reviewer recovery handoff dispatched"})
        self.task.pop("approved_head")
        cause = {
            "task_id": "ODP-TEST-MG-001",
            "from": "Codex",
            "to": "Claude2",
            "message": "Merge group failed for PR #756; fresh review required.",
            "status": "pending",
            "created_at": "2026-09-06T01:30:00Z",
            "reason": "merge_group_failure",
            "run_id": 31321422749,
            "queue_ref": "refs/heads/gh-readonly-queue/dev/pr-756-a297b2a0",
            "head_sha": self.GROUP_HEAD_OLD,
            "pr_number": 756,
            "source_head": self.APPROVED_HEAD,
        }
        green = {
            "state": "OPEN",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }

        def eligible(*, task_overrides=None, drop=(), snapshot_overrides=None):
            board = json.loads(json.dumps(self.status))
            board["tasks"][0].update(task_overrides or {})
            for field in drop:
                board["tasks"][0].pop(field, None)
            board["handoffs"] = [cause]
            status_path.write_text(json.dumps(board), encoding="utf-8")
            snapshot = supervisor.load_status(self.config)["tasks"][0]
            snapshot.update(snapshot_overrides or {})
            ci_reads: list[str] = []

            def gh_view(args, *, cwd=None):
                if args[:2] == ["pr", "view"]:
                    ci_reads.append(args[2])
                    return dict(green)
                return None

            ai_status.clear_ai_status_caches()
            self.addCleanup(ai_status.clear_ai_status_caches)
            with (
                mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
                mock.patch.object(ai_status, "run_gh_json_command", side_effect=gh_view),
                mock.patch.object(ai_status, "status_runtime_config", return_value={}),
            ):
                verdict = dispatch_engine.is_task_review_dispatch_eligible(self.config, snapshot, "Claude2")
            # Reading the canonical store never rewrites it.
            self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")), board)
            return verdict, ci_reads

        # The committed recovery for PR 756 reads exactly PR 756's checks.
        self.assertEqual(eligible(), (True, ["756"]))

        refused = {
            "canonical task PR drifted to a green PR": {"task_overrides": {"pr_number": 757}},
            "canonical task PR missing": {"drop": ("pr_number",)},
            "canonical task PR malformed": {"task_overrides": {"pr_number": "756abc"}},
            "canonical task PR empty": {"task_overrides": {"pr_number": None}},
            "canonical task in another repository": {"task_overrides": {"repository": "oday-data-platform"}},
            "caller snapshot names another PR": {"snapshot_overrides": {"pr_number": 757}},
            "caller snapshot without PR": {"snapshot_overrides": {"pr_number": None}},
            "caller snapshot in another repository": {"snapshot_overrides": {"repository": "oday-data-platform"}},
        }
        for label, case in refused.items():
            with self.subTest(label):
                self.assertEqual(eligible(**case), (False, []))

        # The shared identity itself: top-level PR drift, absence or a bad
        # shape is no recovery for any caller (owner-return, publisher, reality).
        task = dict(self.task, pr_number=756)
        self.assertIsNotNone(ai_status.merge_group_recovery_cause(task, [cause]))
        for value in (757, None, "", "756abc", 0, -756):
            with self.subTest(top_level_pr=value):
                self.assertIsNone(ai_status.merge_group_recovery_cause(dict(task, pr_number=value), [cause]))
        self.assertIsNone(ai_status.merge_group_recovery_cause(
            {k: v for k, v in task.items() if k != "pr_number"}, [cause]))

    # ------------------------------------------------------------------
    # A cause committed before `source_head` existed
    # ------------------------------------------------------------------

    LEGACY_RUN_ID = 43076558341

    def _legacy_board(self, **cause_overrides) -> tuple[Path, dict, dict]:
        """The demoted task with a committed recovery cause that names no source head."""
        status_path = self._canonical_store()
        route = self._route_approved_head()
        self.task.update({"status": "review", "next": "Merge group failed; reviewer recovery handoff dispatched"})
        self.task.pop("approved_head")
        cause = {
            "task_id": "ODP-TEST-MG-001",
            "from": "Codex",
            "to": "Claude2",
            "message": "Merge group failed for PR #756; fresh review required.",
            "status": "pending",
            "created_at": "2026-09-06T01:30:00Z",
            "reason": "merge_group_failure",
            "run_id": self.LEGACY_RUN_ID,
            "queue_ref": "gh-readonly-queue/dev/pr-756-a297b2a0",
            "head_sha": self.GROUP_HEAD_OLD,
            "pr_number": 756,
        }
        cause.update(cause_overrides)
        self.status["handoffs"] = [cause]
        status_path.write_text(json.dumps(self.status), encoding="utf-8")
        return status_path, route, cause

    def _legacy_run(self, **overrides):
        return self._failure_run(**{
            "id": self.LEGACY_RUN_ID,
            "head_branch": "gh-readonly-queue/dev/pr-756-a297b2a0",
            "repository": {"full_name": "o/r"},
            **overrides,
        })

    @staticmethod
    def _board_facts(board: dict) -> tuple:
        """What recovery owns on the board, without write bookkeeping."""
        task = {
            k: v for k, v in board["tasks"][0].items()
            if k not in {"last_update", "priority"}
        }
        return task, board.get("handoffs")

    def _poll(self, status_path: Path, *, gh, stale_status=None):
        """One bus poll of the merge_group producer on the canonical store."""
        import supervisor

        status = stale_status if stale_status is not None else supervisor.load_status(self.config)
        post = mock.Mock(return_value=(True, ""))
        with (
            mock.patch("github_bus.gh_json", side_effect=gh) as gh_json,
            mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._canonical_sync(status_path)) as sync,
            mock.patch.object(ai_status, "post_task_review_status_payload", post),
            mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
            mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
            mock.patch.object(github_reconciliation, "write_activity_log") as write_log,
        ):
            changed = github_reconciliation.poll_merge_group_runs(self.config, self.bus_state, status, "o/r")
        paths = [str(c.args[0][1]) for c in gh_json.call_args_list if len(c.args[0]) > 1]
        return changed, json.loads(status_path.read_text(encoding="utf-8")), {
            "post": post,
            "sync": sync,
            "log": write_log,
            "run_reads": [p for p in paths if p.endswith(f"/actions/runs/{self.LEGACY_RUN_ID}")],
            "run_list_reads": [p for p in paths if "actions/runs?" in p],
        }

    def test_legacy_cause_source_proven_through_canonical_cas_then_reviewer_dispatched_once(self) -> None:
        import supervisor

        status_path, route, cause = self._legacy_board()
        before = json.loads(status_path.read_text(encoding="utf-8"))
        # Nothing can be dispatched from the legacy cause as committed.
        queued: list[dict] = []
        self._ready_dispatch(queued)
        self.assertEqual(queued, [])

        runs = [self._legacy_run()]
        changed, canonical, seen = self._poll(status_path, gh=self._gh(runs=runs))

        self.assertTrue(changed)
        # The committed cause gains exactly its proven source head; the task,
        # its route (head/at/attempts) and every other handoff field are kept.
        self.assertEqual(canonical["handoffs"], [dict(cause, source_head=self.APPROVED_HEAD)])
        self.assertEqual(canonical["tasks"][0]["merge_route"], route)
        self.assertEqual(self._board_facts(canonical)[0], self._board_facts(before)[0])
        seen["post"].assert_not_called()
        self.assertEqual(self._log_types(seen["log"]), ["merge_group_cause_source_proven"])
        self.assertNotIn("legacy_merge_group_cause_checks", self.bus_state)

        # The ready dispatcher now sends the assigned reviewer exactly once.
        skips = self._ready_dispatch(queued)
        self.assertEqual([(e["task_id"], e["reason"], e["target_agent"]) for e in queued],
                         [("ODP-TEST-MG-001", "review_ready_dispatch", "Claude2")])
        self.assertEqual(skips, [None])
        again: list[dict] = []
        self._ready_dispatch(again, pending_events=queued)
        self.assertEqual(again, [])

        # A later poll neither re-reads the run nor writes again.
        changed, canonical, seen = self._poll(status_path, gh=self._gh(runs=runs))
        self.assertFalse(changed)
        self.assertEqual(seen["run_reads"], [])
        self.assertEqual(canonical["handoffs"], [dict(cause, source_head=self.APPROVED_HEAD)])
        self.assertEqual(canonical["tasks"][0]["merge_route"], route)

        # Enrichment licenses nothing else: a merged PR whose submission was not
        # verified as merged recovery is still refused.
        with (
            mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
            mock.patch.object(ai_status, "task_pr_ci_status", return_value=("MERGED", "success")),
        ):
            self.assertFalse(dispatch_engine.is_task_review_dispatch_eligible(
                self.config, supervisor.load_status(self.config)["tasks"][0], "Claude2"))

    def test_legacy_cause_refuted_or_unresolved_is_left_unchanged(self) -> None:
        refuted = {
            "run in another repository": ({"repository": {"full_name": "x/y"}}, None),
            "run is not a merge_group run": ({"event": "push"}, None),
            "run on another queue ref": ({"head_branch": "gh-readonly-queue/dev/pr-756-ffffffff"}, None),
            "run on another group head": ({"head_sha": self.GROUP_HEAD_NEW}, None),
            "group did not enrol the source head": ({}, [self.QUEUE_BASE_OLD, self.ADVANCED_HEAD]),
            "source head only as the group base": ({}, [self.APPROVED_HEAD, self.ADVANCED_HEAD]),
        }
        unresolved = {
            "run unavailable": (None, None),
            "run without repository": ({"repository": None}, None),
            "run without event": ({"event": None}, None),
            "run re-running": ({"status": "in_progress", "conclusion": None}, None),
            "run succeeded on retry": ({"conclusion": "success"}, None),
            "group parents unknown": ({}, github_bus.GitHubBusError("commit lookup failed")),
            "group parents damaged": ({}, {"sha": self.GROUP_HEAD_OLD, "parents": [{}]}),
        }
        for kind, cases in (("refuted", refuted), ("unresolved", unresolved)):
            for label, (run_overrides, parents) in cases.items():
                with self.subTest(label):
                    self.setUp()
                    status_path, route, cause = self._legacy_board()
                    before = json.loads(status_path.read_text(encoding="utf-8"))
                    runs = [] if run_overrides is None else [self._legacy_run(**run_overrides)]
                    commits = {} if parents is None else {self.GROUP_HEAD_OLD: parents}
                    gh = self._gh(runs=runs, commits=commits)

                    with mock.patch("status_transition.commit_canonical_task_transition") as commit:
                        changed, canonical, seen = self._poll(status_path, gh=gh)
                    self.assertFalse(changed)
                    commit.assert_not_called()
                    seen["post"].assert_not_called()
                    self.assertEqual(self._board_facts(canonical), self._board_facts(before))
                    self.assertEqual(self._log_types(seen["log"]).count(f"merge_group_cause_source_{kind}"), 1)
                    queued: list[dict] = []
                    self._ready_dispatch(queued)
                    self.assertEqual(queued, [])

                    # Nothing is remembered between cycles: the next poll reads
                    # the canonical cause and GitHub again, with the same result.
                    with mock.patch("status_transition.commit_canonical_task_transition") as commit:
                        _, canonical, again = self._poll(status_path, gh=gh)
                    commit.assert_not_called()
                    self.assertEqual(self._board_facts(canonical), self._board_facts(before))
                    self.assertEqual(len(again["run_reads"]), 1)
                    self.assertEqual(self._log_types(again["log"]).count(f"merge_group_cause_source_{kind}"), 1)
                    self.assertNotIn("legacy_merge_group_cause_checks", self.bus_state)

                    # A cause the canonical record later corrects is re-judged
                    # on its current facts, not on an earlier refusal.
                    if kind == "refuted":
                        fixed = self._gh(runs=[self._legacy_run()])
                        changed, canonical, _ = self._poll(status_path, gh=fixed)
                        self.assertTrue(changed)
                        self.assertEqual(canonical["handoffs"][0]["source_head"], self.APPROVED_HEAD)

    def test_legacy_cause_not_bound_to_canonical_task_is_never_fetched(self) -> None:
        cases = {
            "cause to another reviewer": ({"to": "Codex2"}, {}),
            "cause for another PR": ({"pr_number": 757}, {}),
            "queue ref for another PR": ({"queue_ref": "gh-readonly-queue/dev/pr-757-a297b2a0"}, {}),
            "cause older than the route": ({"created_at": "2026-09-06T01:00:00Z"}, {}),
            "resolved cause": ({"status": "done"}, {}),
            "other reason": ({"reason": "manual"}, {}),
            "missing run id": ({"run_id": None}, {}),
            # Resolved after the board binds the real source head.
            "group head equals source head": ({"head_sha": "<source head>"}, {}),
            "task approved again": ({}, {"status": "review_approved", "approved_head": self.APPROVED_HEAD}),
            "route moved to another head": ({}, {"merge_route": None}),
        }
        for label, (cause_overrides, task_overrides) in cases.items():
            with self.subTest(label):
                self.setUp()
                status_path, route, _ = self._legacy_board(**cause_overrides)
                board = json.loads(status_path.read_text(encoding="utf-8"))
                if board["handoffs"][0]["head_sha"] == "<source head>":
                    board["handoffs"][0]["head_sha"] = board["tasks"][0]["review_submission"]["remote_sha"]
                if task_overrides.get("merge_route", route) is None:
                    task_overrides = {"merge_route": dict(route, head=self.ADVANCED_HEAD)}
                board["tasks"][0].update(task_overrides)
                status_path.write_text(json.dumps(board), encoding="utf-8")

                with mock.patch("status_transition.commit_canonical_task_transition") as commit:
                    _, canonical, seen = self._poll(status_path, gh=self._gh(runs=[self._legacy_run()]))
                commit.assert_not_called()
                self.assertEqual(seen["run_reads"], [])
                self.assertEqual(canonical["handoffs"], board["handoffs"])
                self.assertNotIn("merge_group_cause_source_proven", self._log_types(seen["log"]))

        # A second pending handoff makes the cause ambiguous.
        self.setUp()
        status_path, _, cause = self._legacy_board()
        board = json.loads(status_path.read_text(encoding="utf-8"))
        board["handoffs"].append({"task_id": "ODP-TEST-MG-001", "to": "Codex", "status": "pending",
                                  "created_at": "2026-09-06T02:00:00Z", "reason": "manual"})
        status_path.write_text(json.dumps(board), encoding="utf-8")
        _, canonical, seen = self._poll(status_path, gh=self._gh(runs=[self._legacy_run()]))
        self.assertEqual(seen["run_reads"], [])
        self.assertNotIn("source_head", canonical["handoffs"][0])

    def test_legacy_cause_enrichment_cas_reject_has_no_side_effects(self) -> None:
        import supervisor

        status_path, _, _ = self._legacy_board()
        board = json.loads(status_path.read_text(encoding="utf-8"))
        status_path.write_text(json.dumps({**board, "_status_write_revision": "r1"}), encoding="utf-8")
        stale = supervisor.load_status(self.config)
        newer = json.loads(status_path.read_text(encoding="utf-8"))
        newer["_status_write_revision"] = "r2"
        newer["tasks"][0]["next"] = "newer canonical write"
        status_path.write_text(json.dumps(newer), encoding="utf-8")

        changed, canonical, seen = self._poll(
            status_path, gh=self._gh(runs=[self._legacy_run()]), stale_status=stale
        )

        self.assertFalse(changed)
        seen["sync"].assert_not_called()
        seen["post"].assert_not_called()
        self.assertEqual(self._board_facts(canonical), self._board_facts(newer))
        self.assertNotIn("merge_group_cause_source_proven", self._log_types(seen["log"]))
        self.assertNotIn("legacy_merge_group_cause_checks", self.bus_state)
        # The refused cycle stops there; the failure scan waits for a fresh read.
        self.assertEqual(seen["run_list_reads"], [])
        queued: list[dict] = []
        self._ready_dispatch(queued)
        self.assertEqual(queued, [])

    # ------------------------------------------------------------------
    # A recovery whose PR merged before its reviewer decision (B)
    # ------------------------------------------------------------------

    MERGED_AT = "2026-09-06T02:10:00Z"

    def _source_repo(self) -> Path:
        return Path(self.config["coordination"]["repositories"]["pantheon"]["local_path"])

    def _merge_source_into_dev(self) -> str:
        """GitHub's queue merged the exact source head into `dev`."""
        repo = self._source_repo()

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

        git("checkout", "-q", "dev")
        git("merge", "--no-ff", "-q", "-m", "Merge pull request #756", self.APPROVED_HEAD)
        git("update-ref", "refs/remotes/origin/dev", "dev")
        return git("rev-parse", "HEAD")

    def _merged_pr(self, merge_commit: str, **overrides) -> dict:
        pr = {
            "number": 756,
            "state": "MERGED",
            "url": "https://github.com/o/r/pull/756",
            "headRefName": self.task["review_submission"]["branch"],
            "headRefOid": self.APPROVED_HEAD,
            "baseRefName": "dev",
            "isDraft": False,
            "mergedAt": self.MERGED_AT,
            "mergeCommit": {"oid": merge_commit},
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }
        pr.update(overrides)
        return pr

    def _status_command(self, status_path: Path, actor: str, command: str, args: list, *, pr=None) -> list[dict]:
        """One real `ai_status <command>` by `actor`, through the CLI `main`.

        The command commits with the original `sync_all` and status-check
        emission under the status lock. Only GitHub (`run_gh_json_command` and
        the status POST), the config/repository lookups and the status root
        are injected; Git checks and the delivery-identity scan run against
        the synthetic source repository. Returns the appended activity log.
        """
        with self._status_cli_externals(pr=pr):
            return self._ai_status_cli(status_path, [command, *args], actor=actor)

    @contextlib.contextmanager
    def _status_cli_externals(self, *, pr=None):
        """GitHub and repository-registry answers for a CLI run; nothing else."""
        with (
            mock.patch.object(ai_status, "status_runtime_config", return_value={}),
            mock.patch.object(ai_status, "merged_orchestrator_config", return_value=self.config),
            mock.patch.object(ai_status, "task_repository_id", return_value="pantheon"),
            mock.patch.object(ai_status, "repository_local_path", return_value=self._source_repo()),
            mock.patch.object(ai_status, "delivery_merge_target_branch", return_value="dev"),
            mock.patch.object(ai_status, "run_gh_json_command", return_value=pr),
            mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
            mock.patch.object(ai_status, "post_task_review_status_payload", return_value=(True, "")),
            mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
        ):
            yield

    def _proven_legacy_board(self):
        """W's shape: legacy cause, proven by the producer through the real CAS."""
        status_path, route, cause = self._legacy_board()
        changed, _, _ = self._poll(status_path, gh=self._gh(runs=[self._legacy_run()]))
        self.assertTrue(changed)
        return status_path, route, dict(cause, source_head=self.APPROVED_HEAD)

    @staticmethod
    def _pending(board: dict) -> list[dict]:
        return [h for h in board["handoffs"] if h.get("status") == "pending"]

    def test_merged_recovery_owner_return_publish_review_and_approve_keep_one_cause(self) -> None:
        status_path, route, cause = self._proven_legacy_board()
        merge_commit = self._merge_source_into_dev()

        # 1. The PR merged while the submission was verified only open: the
        # reviewer stays refused, and the same tick returns the task to its
        # owner. The return is authorised by GitHub's fresh answer for the
        # exact repo/PR/source head through the provenance reader; the
        # dispatcher's cached two-field CI summary says nothing here.
        import supervisor

        gh_calls: list = []
        queued: list[dict] = []
        skips = self._ready_dispatch(
            queued, pr_status=(None, "unknown"), github_pr=self._merged_pr(merge_commit), gh_calls=gh_calls,
        )
        self.assertTrue(gh_calls)
        for _kind, args in gh_calls:
            self.assertEqual(args[args.index("--repo") + 1], "o/r")
        self.assertIn(
            ["pr", "list", "--head", self.task["review_submission"]["branch"]],
            [args[:4] for _kind, args in gh_calls],
        )
        board = json.loads(status_path.read_text(encoding="utf-8"))
        task = board["tasks"][0]
        self.assertEqual(task["status"], "in_progress")
        self.assertNotIn("approved_head", task)
        self.assertEqual(task["merge_route"], route)
        self.assertEqual(self._pending(board), [cause])
        self.assertNotIn("merged_at", task["review_submission"])
        self.assertIn("task_finalize.sh", task["next"])
        self.assertEqual(task["last_reopened_by"], "Supervisor")
        self.assertEqual(task["last_reopened_reason"], status_transition.REOPEN_REASON_CONTROL_PLANE_RECOVERY)
        self.assertEqual(task["last_reopen_category"], status_transition.REOPEN_CATEGORY_CONTROL_PLANE_RECOVERY)
        # The status action is the original owner-return record.
        activity = [
            json.loads(line)
            for line in Path(self.config["paths"]["activity_log"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        repaired = [e for e in activity if e.get("type") == "review_submission_repaired"]
        self.assertEqual(
            [(e["task_id"], e["entry"], e["reason"]) for e in repaired],
            [("ODP-TEST-MG-001", "merge_group_merged_publication",
              status_transition.REOPEN_REASON_CONTROL_PLANE_RECOVERY)],
        )
        # The same tick wakes exactly the fixture owner, through the original
        # owned_in_progress lane -- never the reviewer, never nobody.
        self.assertEqual(
            [(e["task_id"], e["reason"], e["target_agent"]) for e in queued],
            [("ODP-TEST-MG-001", "owned_in_progress_dispatch", "Codex")],
        )
        self.assertEqual(skips, [None])
        owner_event = queued[0]

        def owner_key_now():
            # The consumer's re-derivation against the canonical board, with
            # the task branch still at the head the fake remote reports.
            with mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD):
                current = dispatch_engine.task_index_from_status(self.config, supervisor.load_status(self.config))
                return current, dispatch_engine.current_dispatch_event_key(self.config, owner_event, current)

        task_map, key = owner_key_now()
        self.assertEqual(key, owner_event["key"])
        owner_worker = {"task_id": "ODP-TEST-MG-001", "agent_id": "codex", "reason": owner_event["reason"]}
        reviewer_worker = {"task_id": "ODP-TEST-MG-001", "agent_id": "claude2", "reason": owner_event["reason"]}
        self.assertTrue(dispatch_engine.worker_matches_current_assignment(self.config, owner_worker, task_map))
        self.assertFalse(dispatch_engine.worker_matches_current_assignment(self.config, reviewer_worker, task_map))

        # The owner-return was committed by the original CAS and the original
        # `sync_status_pipeline`, whose launcher ran the real CLI `sync`.
        self.assertIn((["sync"], None, 0), [(c[0], None, c[2]) for c in self.launcher_calls])

        # 2. The queue consumer started the owner's worker: its original status
        # action for the owned_in_progress lane runs `ai-status.sh progress`
        # as the fixture owner, and the real CLI commits it with `sync_all`.
        self.launcher_calls.clear()
        with self._status_cli_externals():
            self.assertTrue(status_transition.sync_dispatched_task_status(self.config, owner_event))
        progress_message = "Supervisor re-dispatched ODP-TEST-MG-001; task remains in progress."
        self.assertEqual(self.launcher_calls, [(["progress", "ODP-TEST-MG-001", progress_message], "Codex", 0)])
        activity = [
            json.loads(line)
            for line in Path(self.config["paths"]["activity_log"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [(e["agent"], e["task_id"], e["message"]) for e in activity if e.get("type") == "progress"],
            [("Codex", "ODP-TEST-MG-001", progress_message)],
        )
        self.assertEqual(
            [(e["target_agent"], e["dispatch_reason"]) for e in activity if e.get("type") == "task_dispatch_synced"],
            [("Codex", "owned_in_progress_dispatch")],
        )
        board = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(board["tasks"][0]["status"], "in_progress")
        self.assertEqual(board["tasks"][0]["owner"], "Codex")
        self.assertEqual(board["tasks"][0]["next"], progress_message)
        self.assertEqual(self._pending(board), [cause])
        self.assertEqual(board["tasks"][0]["merge_route"], route)
        task_map, key = owner_key_now()
        self.assertEqual(key, owner_event["key"])
        self.assertTrue(dispatch_engine.worker_matches_current_assignment(self.config, owner_worker, task_map))

        # The reviewer is not the owner; the same action refuses to act for it.
        with self._status_cli_externals():
            self.assertFalse(status_transition.sync_dispatched_task_status(
                self.config, dict(owner_event, target_agent="claude2", target_display_name="Claude2")))
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8")), board)

        # The original canonical sync (`sync_all`) keeps that one cause too.
        self.launcher_calls.clear()
        with self._status_cli_externals():
            self.assertTrue(status_transition.sync_status_pipeline(self.config))
        self.assertEqual([(c[0], c[2]) for c in self.launcher_calls], [(["sync"], 0)])
        board = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(board["tasks"][0]["status"], "in_progress")
        self.assertEqual(self._pending(board), [cause])
        self.assertEqual(board["tasks"][0]["merge_route"], route)
        task_map, key = owner_key_now()
        self.assertEqual(key, owner_event["key"])
        self.assertTrue(dispatch_engine.worker_matches_current_assignment(self.config, owner_worker, task_map))

        # A second tick neither repeats the return nor wakes the reviewer; the
        # owner wake already queued is not queued again.
        again: list[dict] = []
        self._ready_dispatch(again, pending_events=queued, github_pr=self._merged_pr(merge_commit))
        self.assertEqual(again, [])
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]["status"], "in_progress")

        # 3. The owner's real publisher verifies the merge for the same PR/head.
        logs = self._status_command(
            status_path, "Codex", "submit_review",
            ["ODP-TEST-MG-001", "756", "Publish merged recovery"], pr=self._merged_pr(merge_commit),
        )
        board = json.loads(status_path.read_text(encoding="utf-8"))
        task = board["tasks"][0]
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["merge_route"], route)
        submission = task["review_submission"]
        self.assertEqual(submission["remote_sha"], self.APPROVED_HEAD)
        self.assertEqual(submission["merged_at"], self.MERGED_AT)
        self.assertEqual(submission["merge_commit"], merge_commit)
        self.assertEqual(submission["recovery_by"], "Codex")
        self.assertEqual(submission["source_authors"], ["Codex"])
        # The committed cause is the reviewer handoff; no generic one is added.
        self.assertEqual(self._pending(board), [cause])
        self.assertEqual(
            [e.get("merge_group_recovery_publication") for e in logs if e["type"] == "review_submission"],
            [True],
        )
        # The next canonical sync after the publisher keeps the published
        # phase, the route and the one cause.
        with self._status_cli_externals():
            self.assertTrue(status_transition.sync_status_pipeline(self.config))
        board = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(board["tasks"][0]["status"], "review")
        self.assertEqual(board["tasks"][0]["review_submission"], submission)
        self.assertEqual(board["tasks"][0]["merge_route"], route)
        self.assertEqual(self._pending(board), [cause])

        # 4. The assigned reviewer is dispatched exactly once for the merged head.
        reviewer: list[dict] = []
        skips = self._ready_dispatch(reviewer, pr_status=("MERGED", "success"))
        self.assertEqual([(e["task_id"], e["reason"], e["target_agent"]) for e in reviewer],
                         [("ODP-TEST-MG-001", "review_ready_dispatch", "Claude2")])
        self.assertEqual(skips, [None])
        duplicate: list[dict] = []
        self._ready_dispatch(duplicate, pending_events=reviewer, pr_status=("MERGED", "success"))
        self.assertEqual(duplicate, [])

        # 5. Only the reviewer's approval closes the cause and opens finalize.
        with mock.patch.object(ai_status, "resolve_immutable_merged_task_sha", return_value=self.APPROVED_HEAD):
            self._status_command(
                status_path, "Claude2", "approve",
                ["ODP-TEST-MG-001", "Merged head re-reviewed after the group failure"],
                pr=self._merged_pr(merge_commit),
            )
        board = json.loads(status_path.read_text(encoding="utf-8"))
        task = board["tasks"][0]
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], self.APPROVED_HEAD)
        pending = self._pending(board)
        self.assertEqual([(h["to"], h.get("reason")) for h in pending], [("Codex", pending[0].get("reason"))])
        self.assertNotEqual(pending[0].get("reason"), "merge_group_failure")
        self.assertEqual(
            [h for h in board["handoffs"] if h.get("reason") == "merge_group_failure"][0]["status"], "done"
        )
        self.assertIsNone(ai_status.merge_group_recovery_cause(task, board["handoffs"]))

    @staticmethod
    def _recovery_facts(board) -> dict:
        """Everything the owner-return could write: task phase, provenance, route, handoffs."""
        if isinstance(board, Path):
            board = json.loads(board.read_text(encoding="utf-8"))
        task = board["tasks"][0]
        return {
            "task": {
                key: task.get(key)
                for key in (
                    "status", "owner", "reviewer", "waiting_for", "approved_head", "next", "pr_number",
                    "review_submission", "merge_route", "last_reopened_by", "last_reopened_reason",
                    "last_reopen_category", "last_reopened_at", "helper_execution_lease",
                )
            },
            "handoffs": board["handoffs"],
        }

    def test_merged_recovery_owner_return_refuses_everything_else(self) -> None:
        import runtime_state
        import supervisor

        def busy_worker(state):
            state.setdefault("workers", {})["run-1"] = {
                "run_id": "run-1", "status": "running", "agent_id": "codex",
                "task_id": "ODP-TEST-MG-001", "pid": os.getpid(),
            }
            return state

        merged = lambda mc, **o: lambda: self._merged_pr(mc, **o)  # noqa: E731
        check = lambda **o: [dict({"__typename": "CheckRun", "name": "product", "status": "COMPLETED",  # noqa: E731
                                   "conclusion": "SUCCESS"}, **o)]
        # label -> (GitHub's answer for the PR, task overrides, variant)
        cases = {
            # Facts that are not the publisher's own MERGED/CI rule.
            "PR still open": (lambda mc: merged(mc, state="OPEN", mergedAt=None, mergeCommit=None), {}, None),
            "merged PR with failing CI": (lambda mc: merged(mc, statusCheckRollup=check(conclusion="FAILURE")), {}, None),
            "merged PR with CI pending": (lambda mc: merged(mc, statusCheckRollup=check(status="IN_PROGRESS",
                                                                                         conclusion=None)), {}, None),
            "merged PR with an unrecognised check type": (
                lambda mc: merged(mc, statusCheckRollup=check(__typename="Mystery")), {}, None),
            "merged PR without any check": (lambda mc: merged(mc, statusCheckRollup=[]), {}, None),
            "merged PR without merge time": (lambda mc: merged(mc, mergedAt=None), {}, None),
            "merged PR without merge commit": (lambda mc: merged(mc, mergeCommit=None), {}, None),
            "merged PR with a malformed merge commit": (lambda mc: merged(mc, mergeCommit={"oid": "zz"}), {}, None),
            # Identity drift between GitHub's answer and the recorded recovery.
            "same PR merged another source head": (
                lambda mc: merged(mc, headRefOid=self.ADVANCED_HEAD), {}, None),
            "merge commit offered as the source head": (lambda mc: merged(mc, headRefOid=mc), {}, None),
            "another PR number": (lambda mc: merged(mc, number=757, url="https://github.com/o/r/pull/757"), {}, None),
            "another repository": (lambda mc: merged(mc, url="https://github.com/x/r/pull/756"), {}, None),
            "another head branch": (lambda mc: merged(mc, headRefName="task/other"), {}, None),
            "another base branch": (lambda mc: merged(mc, baseRefName="main"), {}, None),
            "task PR number drifted": (lambda mc: merged(mc), {"pr_number": 757}, None),
            "task PR number missing": (lambda mc: merged(mc), {"pr_number": None}, None),
            # GitHub did not answer, or answered with nothing usable. The
            # dispatcher's cached summary still says MERGED/success; it is not
            # asked.
            "GitHub unreachable (stale cache says merged)": (lambda mc: None, {}, None),
            "malformed answer": (lambda mc: lambda: {"number": "756"}, {}, None),
            "PR number of the wrong type": (lambda mc: merged(mc, number=True), {}, None),
            # Not this narrow case at all.
            # Already published: the owed reviewer is the one woken, as before.
            "submission already verified merged": (
                lambda mc: merged(mc), {"review_submission": {"merged_at": self.MERGED_AT}}, "verified"),
            "helper lease held": (lambda mc: merged(mc), {"helper_execution_lease": {
                "claimed_by": "Codex", "lease_expires_at": "2099-01-01T00:00:00Z"}}, None),
            "human gate": (lambda mc: merged(mc), {"task_class": "human_gate"}, None),
            "not dispatchable": (lambda mc: merged(mc), {"non_dispatchable": True}, None),
            "worker active on the task": (lambda mc: merged(mc), {}, "busy"),
            "delivery pending for the task": (lambda mc: merged(mc), {}, "pending"),
            "cause not yet proven": (lambda mc: merged(mc), {}, "legacy"),
            "no committed cause": (lambda mc: merged(mc), {}, "no_cause"),
        }
        for label, (github_pr, task_overrides, variant) in cases.items():
            with self.subTest(label):
                self.setUp()
                if variant == "legacy":
                    status_path, route, cause = self._legacy_board()
                else:
                    status_path, route, cause = self._proven_legacy_board()
                merge_commit = self._merge_source_into_dev()
                board = json.loads(status_path.read_text(encoding="utf-8"))
                for key, value in task_overrides.items():
                    if isinstance(value, dict) and isinstance(board["tasks"][0].get(key), dict):
                        board["tasks"][0][key] = {**board["tasks"][0][key], **value}
                    else:
                        board["tasks"][0][key] = value
                if variant == "no_cause":
                    board["handoffs"] = []
                status_path.write_text(json.dumps(board), encoding="utf-8")
                activity_log = Path(self.config["paths"]["activity_log"])
                activity_before = activity_log.read_text(encoding="utf-8") if activity_log.exists() else ""
                kwargs = {"pr_status": ("MERGED", "success"), "github_pr": github_pr(merge_commit)}
                if variant == "busy":
                    kwargs["state"] = busy_worker(runtime_state.default_state())
                if variant == "pending":
                    kwargs["pending_events"] = [{"task_id": "ODP-TEST-MG-001", "key": "other-event"}]

                queued: list[dict] = []
                with mock.patch("github_bus.gh_json", side_effect=github_bus.GitHubBusOffline("offline")):
                    self._ready_dispatch(queued, **kwargs)
                # Zero mutation, zero CAS write, zero dispatch.
                self.maxDiff = None
                after_facts, before_facts = self._recovery_facts(status_path), self._recovery_facts(board)
                if label == "helper lease held":
                    # The lease held the return off for this tick; the same
                    # tick's original dead-claim cleanup then releases a lease
                    # with no launched run -- that is not the owner-return.
                    after_facts["task"].pop("helper_execution_lease")
                    before_facts["task"].pop("helper_execution_lease")
                self.assertEqual(after_facts, before_facts)
                after_log = activity_log.read_text(encoding="utf-8") if activity_log.exists() else ""
                self.assertNotIn("review_submission_repaired", after_log[len(activity_before):])
                self.assertEqual(
                    [(e["reason"], e["target_agent"]) for e in queued if e["task_id"] == "ODP-TEST-MG-001"],
                    [("review_ready_dispatch", "Claude2")] if variant == "verified" else [],
                )

        # A fresh read every tick: an earlier merged answer does not carry
        # over once GitHub's answer for the same PR changes.
        self.setUp()
        status_path, route, _ = self._proven_legacy_board()
        merge_commit = self._merge_source_into_dev()
        board = json.loads(status_path.read_text(encoding="utf-8"))
        answers = iter([None, None, None, None])
        calls: list = []
        self._ready_dispatch([], github_pr=lambda: next(answers, None), gh_calls=calls)
        self.assertTrue(calls)
        self.assertEqual(self._recovery_facts(status_path), self._recovery_facts(board))
        calls.clear()
        queued: list[dict] = []
        self._ready_dispatch(queued, github_pr=self._merged_pr(merge_commit), gh_calls=calls)
        self.assertTrue(calls)
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]["status"], "in_progress")
        self.assertEqual([e["target_agent"] for e in queued], ["Codex"])

        # The wrapper without the dispatch pass's runtime state cannot tell a
        # busy task apart, so it moves nothing.
        self.setUp()
        status_path, route, _ = self._proven_legacy_board()
        status = supervisor.load_status(self.config)
        reader = mock.MagicMock(side_effect=AssertionError("no state, no read"))
        with mock.patch.object(ai_status, "pull_request_status_for_branch", reader):
            self.assertFalse(supervisor.repair_unsubmitted_review_tasks(self.config, status))
        reader.assert_not_called()
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["tasks"][0]["status"], "review")

    def test_merged_recovery_publication_negative_paths(self) -> None:
        def owner_returned():
            self.setUp()
            status_path, route, cause = self._proven_legacy_board()
            merge_commit = self._merge_source_into_dev()
            queued: list[dict] = []
            self._ready_dispatch(queued, github_pr=self._merged_pr(merge_commit))
            self.assertEqual([e["target_agent"] for e in queued], ["Codex"])
            board = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(board["tasks"][0]["status"], "in_progress")
            return status_path, route, cause, merge_commit, board

        with self.subTest("a new head is an ordinary submission"):
            status_path, route, cause, merge_commit, _ = owner_returned()
            board = json.loads(status_path.read_text(encoding="utf-8"))
            # The owner moved the branch: the recovery identity no longer holds.
            board["tasks"][0]["review_submission"]["remote_sha"] = self.ADVANCED_HEAD
            self.assertIsNone(ai_status.merge_group_recovery_cause(board["tasks"][0], board["handoffs"]))
            ai_status.normalize_handoffs(board)
            self.assertEqual(self._pending(board), [])

        for label, actor, pr in (
            ("merge commit not reachable from dev", "Codex", lambda mc: self._merged_pr("f" * 40)),
            ("merged PR without merge time", "Codex", lambda mc: self._merged_pr(mc, mergedAt=None)),
            ("merged PR with failing CI", "Codex", lambda mc: self._merged_pr(mc, statusCheckRollup=[
                {"__typename": "CheckRun", "name": "product", "status": "COMPLETED", "conclusion": "FAILURE"}])),
            ("reviewer cannot publish for the owner", "Claude2", lambda mc: self._merged_pr(mc)),
        ):
            with self.subTest(label):
                status_path, route, cause, merge_commit, board = owner_returned()
                with self.assertRaises(SystemExit):
                    self._status_command(
                        status_path, actor, "submit_review",
                        ["ODP-TEST-MG-001", "756", "Publish merged recovery"], pr=pr(merge_commit),
                    )
                after = json.loads(status_path.read_text(encoding="utf-8"))
                self.assertEqual(after, board)

        with self.subTest("a rejected closeout seal keeps cause and route for the retry"):
            import supervisor

            status_path, route, cause, merge_commit, _ = owner_returned()
            self._status_command(
                status_path, "Codex", "submit_review",
                ["ODP-TEST-MG-001", "756", "Publish merged recovery"], pr=self._merged_pr(merge_commit),
            )
            status = supervisor.load_status(self.config)
            task = status["tasks"][0]
            with (
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._canonical_sync(status_path)),
                mock.patch.object(ai_status, "post_task_review_status_payload", return_value=(True, "")),
                mock.patch.object(ai_status, "resolve_task_sha", return_value=self.APPROVED_HEAD),
                mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
            ):
                self.assertTrue(status_transition.reject_unsealed_worker_handoff(
                    self.config, status, task, worker_run_id="run-1", reason="dirty_worktree", detail="x"))
            board = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(board["tasks"][0]["status"], "in_progress")
            self.assertEqual(board["tasks"][0]["merge_route"], route)
            self.assertEqual(self._pending(board), [cause])
            # The same owner republishes; still one cause, no generic handoff.
            self._status_command(
                status_path, "Codex", "submit_review",
                ["ODP-TEST-MG-001", "756", "Republish after seal"], pr=self._merged_pr(merge_commit),
            )
            board = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(board["tasks"][0]["status"], "review")
            self.assertEqual(self._pending(board), [cause])
            self.assertEqual(board["tasks"][0]["merge_route"], route)

    def test_task_reality_keeps_recovery_route_and_clears_ordinary_merged_route(self) -> None:
        status_path, route, cause = self._legacy_board()
        board = json.loads(status_path.read_text(encoding="utf-8"))
        board["tasks"][0]["pr_number"] = 756
        ordinary = json.loads(json.dumps(board["tasks"][0]))
        ordinary.update({"id": "ODP-TEST-MG-002", "review_submission": dict(ordinary["review_submission"], pr_number=757),
                         "merge_route": dict(route, pr_number=757), "pr_number": 757,
                         "branch": "task/odp-test-mg-002"})
        board["tasks"].append(ordinary)
        status_path.write_text(json.dumps(board), encoding="utf-8")
        merged = mock.Mock(stdout=json.dumps({"number": 756, "state": "MERGED", "mergedAt": self.MERGED_AT,
                                              "headRefName": "x"}))
        calls: list[list] = []

        def run_gh(args, **_kwargs):
            calls.append(list(args))
            return merged

        import supervisor

        status = supervisor.load_status(self.config)
        with (
            mock.patch("github_bus.run_gh", side_effect=run_gh),
            mock.patch.object(dispatch_engine, "_this_repository_slug", return_value="o/r"),
            mock.patch.object(dispatch_engine, "_remote_branch_names",
                              return_value={"task/odp-test-mg-001", "task/odp-test-mg-002"}),
            mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._canonical_sync(status_path)),
            mock.patch.object(ai_status, "post_task_review_status_payload", return_value=(True, "")),
            mock.patch.object(ai_status, "task_repository_slug_safe", return_value="o/r"),
            mock.patch.object(dispatch_engine, "write_activity_log"),
        ):
            self.assertTrue(dispatch_engine.reconcile_task_reality(self.config, status))
        # `gh pr view` is asked for fields it has; `merged` is derived.
        self.assertTrue(calls and all("mergedAt" in a[-1] and "merged," not in a[-1] for a in calls))
        after = {t["id"]: t for t in json.loads(status_path.read_text(encoding="utf-8"))["tasks"]}
        self.assertEqual(after["ODP-TEST-MG-001"]["merge_route"], route)
        self.assertNotIn("merge_route", after["ODP-TEST-MG-002"])

    def test_supervisor_merge_group_reconciliation_exports_and_behavior(self) -> None:
        """Verify supervisor exports and delegates merge group reconciliation correctly."""
        import supervisor

        self.assertTrue(callable(supervisor.parse_merge_group_pr_number))
        self.assertTrue(callable(supervisor.correlate_merge_group_task))
        self.assertTrue(callable(supervisor.reconcile_merge_group_runs))
        self.assertTrue(callable(supervisor.poll_merge_group_runs))

        self.assertEqual(
            supervisor.parse_merge_group_pr_number("refs/heads/gh-readonly-queue/dev/pr-555-abc"),
            555,
        )

        approved_head = "abc123450000000000000000000000000000dead"
        task = {
            "id": "ODP-SUP-MG-001",
            "status": "review_approved",
            "owner": "Antigravity5",
            "reviewer": "Claude2",
            "pr_number": 555,
            "approved_head": approved_head,
        }
        status = {"tasks": [task], "handoffs": []}
        bus_state = {"processed_merge_group_run_ids": [], "tasks": {}}
        # A merge group head is the temporary merge commit GitHub builds for the
        # queue entry, never the PR head itself.
        run = {
            "id": 999111,
            "head_branch": "gh-readonly-queue/dev/pr-555-abc",
            "head_sha": "77aa11bb0000000000000000000000000000c0de",
            "conclusion": "failure",
            "status": "completed",
            "name": "CI",
            "workflow_id": 100,
        }
        pr_node = {
            "number": 555,
            "state": "OPEN",
            "merged": False,
            "mergedAt": None,
            "headRefOid": approved_head,
            "isInMergeQueue": False,
            "mergeQueueEntry": None,
        }

        def fake_gh_json(args, **kwargs):
            if args[:2] == ["api", "graphql"]:
                return {"data": {"repository": {"pullRequest": pr_node}}}
            if args[:1] == ["api"] and "actions/runs" in str(args[1]):
                return {"total_count": 1, "workflow_runs": [run]}
            if args[:1] == ["api"] and "/commits/" in str(args[1]):
                return {
                    "sha": run["head_sha"],
                    "parents": [
                        {"sha": "base000000000000000000000000000000000000"},
                        {"sha": approved_head},
                    ],
                }
            return None

        with (
            mock.patch("github_bus.gh_json", side_effect=fake_gh_json),
            mock.patch("github_reconciliation.write_activity_log") as log,
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch(
                "github_reconciliation.runtime_ai_status.emit_task_review_status_check"
            ),
        ):
            changed = supervisor.reconcile_merge_group_runs(
                {"github_bus": {"enabled": True}},
                bus_state,
                status,
                "o/r",
                [run],
            )

        self.assertTrue(changed)
        self.assertEqual(task["status"], "review")
        self.assertIsNone(task.get("approved_head"))
        self.assertEqual(len(status["handoffs"]), 1)
        self.assertEqual(status["handoffs"][0]["to"], "Claude2")
        self.assertEqual(status["handoffs"][0]["from"], "Antigravity5")
        self.assertEqual(status["handoffs"][0]["reason"], "merge_group_failure")
        log.assert_called_once()
        self.assertEqual(log.call_args.args[1]["type"], "merge_group_failure_reconciled")

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def test_poll_merge_group_runs_integrates_with_gh_json(self) -> None:
        run_fixture = self._failure_run(id=70001)

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=[run_fixture])) as gh_json,
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            changed = github_reconciliation.poll_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r"
            )

        self.assertTrue(changed)
        self.assertEqual(
            gh_json.call_args_list[0].args[0],
            ["api", "repos/o/r/actions/runs?event=merge_group&per_page=20"],
        )
        self.assertEqual(len(self.status["handoffs"]), 1)

    def test_reconcile_merge_group_reads_each_api_once_per_batch(self) -> None:
        """The pre-CAS re-read is one extra call per batch, not one per run."""
        first = self._failure_run(id=80001)
        second = self._failure_run(
            id=80002,
            head_branch="refs/heads/gh-readonly-queue/dev/pr-756-b41f0c9d",
            head_sha=self.GROUP_HEAD_NEW,
            created_at="2026-09-06T01:26:00Z",
        )
        runs = [first, second]

        with (
            mock.patch("github_bus.gh_json", side_effect=self._gh(runs=runs)) as gh_json,
            mock.patch("github_reconciliation.write_activity_log"),
            mock.patch("status_transition.commit_canonical_task_transition", return_value=True),
            mock.patch("github_reconciliation.runtime_ai_status.emit_task_review_status_check"),
        ):
            github_reconciliation.reconcile_merge_group_runs(
                self.config, self.bus_state, self.status, "o/r", runs
            )

        runs_calls = [
            call for call in gh_json.call_args_list
            if "actions/runs" in str(call.args[0][-1])
        ]
        self.assertEqual(len(runs_calls), 1)

    def test_sync_github_bus_executes_poll_merge_group_runs(self) -> None:
        runtime_state = {}
        with (
            mock.patch.object(github_bus, "load_bus_state", return_value={"tasks": {}}),
            mock.patch.object(github_bus, "save_bus_state"),
            mock.patch.object(github_bus, "should_skip_for_offline_backoff", return_value=False),
            mock.patch.object(github_bus, "infer_repo_slug", return_value="o/r"),
            mock.patch.object(github_bus, "load_status", return_value=self.status),
            mock.patch.object(github_bus, "sync_outbound", return_value=False),
            mock.patch.object(github_bus, "sync_coordination_outbound", return_value=False),
            mock.patch.object(github_bus, "poll_pr_reviews", return_value=False),
            mock.patch.object(github_bus, "poll_merge_group_runs", return_value=True) as poll_mg,
            mock.patch.object(github_bus, "poll_issue_comments", return_value=False),
            mock.patch.object(github_bus, "poll_coordination_issue_comments", return_value=False),
            mock.patch.object(github_bus, "consume_cloud_relay_commands", return_value=False),
            mock.patch.object(github_bus, "push_cloud_relay_digest"),
        ):
            changed = github_bus.sync_github_bus(self.config, runtime_state)

        self.assertTrue(changed)
        poll_mg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
