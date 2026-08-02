#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import github_bus
from github_command_parser import GitHubCommand


class GitHubBusCommandTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def test_upsert_review_pr_create_uses_create_label_flags(self) -> None:
        config = {
            "github_bus": {
                "default_branch": "master",
                "auto_request_reviewers": True,
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
            mock.patch.object(github_bus, "remote_branch_exists", return_value=True),
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
        args = run_gh.call_args.args[0]
        self.assertIn("--label", args)
        self.assertNotIn("--add-label", args)

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
            mock.patch.object(github_bus, "write_activity_log") as write_activity_log,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertTrue(changed)
        entry = bus_state["tasks"]["LIN-001"]["review_pr"]
        self.assertEqual(entry["state"], "skipped_unpublished_branch")
        self.assertEqual(entry["branch"], "feature/lin-001")
        self.assertEqual(entry["head_sha"], "abc123")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "github_review_pr_skipped")

    def test_upsert_review_pr_skips_recent_remote_recheck_for_unpublished_branch(self) -> None:
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
            mock.patch.object(github_bus, "remote_branch_exists") as remote_branch_exists,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_exists.assert_not_called()

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
            mock.patch.object(github_bus, "remote_branch_exists", return_value=False) as remote_branch_exists,
        ):
            changed = github_bus.upsert_review_pr(config, bus_state, status, "ajoe734/pantheon", task)

        self.assertFalse(changed)
        remote_branch_exists.assert_called_once_with("feature/lin-001")


class GitHubBusProcessTests(unittest.TestCase):
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

    def test_run_gh_uses_vendored_wrapper_when_system_gh_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            vendored = root / ".orchestrator" / "bin" / "gh"
            vendored.parent.mkdir(parents=True, exist_ok=True)
            vendored.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            vendored.chmod(0o755)

            with (
                mock.patch.object(github_bus, "ROOT", root),
                mock.patch.object(github_bus, "command_exists", return_value=None),
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
        queue = github_bus.load_jsonl(Path(self.config["paths"]["event_queue"]))
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
        queue = github_bus.load_jsonl(Path(self.config["paths"]["event_queue"]))
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


if __name__ == "__main__":
    unittest.main()
