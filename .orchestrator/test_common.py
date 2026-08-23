#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import common


class PlanningSharedFilesTests(unittest.TestCase):
    def test_planning_shared_files_follow_active_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            planning_dir = root / "docs" / "02-architecture" / "consensus" / "sessions" / "phase3-test"
            planning_dir.mkdir(parents=True)
            readme = planning_dir / "README.md"
            session_file = planning_dir / "planning-session.json"
            state_file = root / ".orchestrator" / "planning-state.json"
            state_file.parent.mkdir(parents=True)
            readme.write_text("# phase3\n", encoding="utf-8")
            session_file.write_text("{}", encoding="utf-8")
            state_file.write_text(
                json.dumps(
                    {
                        "status": "active",
                        "session_file": str(session_file),
                        "artifacts": {
                            "planning_readme": {
                                "path": str(readme),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(common, "PLANNING_STATE_PATH", state_file):
                files = common.planning_shared_files()

        self.assertEqual(files, [readme, session_file])


class JsonLoadResilienceTests(unittest.TestCase):
    def test_load_json_retries_after_transient_decode_error(self) -> None:
        payload = {"ok": True}
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "read_text", side_effect=['{"broken": 1}{"extra": 2}', json.dumps(payload)]),
            mock.patch.object(common.time, "sleep") as sleep,
        ):
            result = common.load_json(Path("/tmp/transient.json"), default={})

        self.assertEqual(result, payload)
        sleep.assert_called_once()

    def test_comment_stripper_preserves_urls_and_still_strips_comments(self) -> None:
        document = json.dumps({"pr_url": "https://github.com/alfloop-dev/odayplus/pull/505"}, indent=2)
        self.assertEqual(common.strip_json_comments(document), document)

        commented = '{\n  // lead\n  "a": 1, /* block */ "b": "x//y"\n}'
        self.assertEqual(json.loads(common.strip_json_comments(commented)), {"a": 1, "b": "x//y"})

    def test_load_json_reports_the_error_from_the_file_as_written(self) -> None:
        # A state file with two stray trailing bytes must report the trailing
        # garbage, not a phantom defect the sanitizer introduced elsewhere.
        corrupt = json.dumps({"pr_url": "https://github.com/alfloop-dev/odayplus/pull/505"}, indent=2) + "\\n"
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "read_text", return_value=corrupt),
            mock.patch.object(common.time, "sleep"),
            self.assertRaises(json.JSONDecodeError) as raised,
        ):
            common.load_json(Path("/tmp/corrupt.json"), default={})

        self.assertIn("Extra data", str(raised.exception))
        self.assertNotIn("control character", str(raised.exception))

    def test_load_jsonl_retries_after_transient_decode_error(self) -> None:
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(
                Path,
                "read_text",
                side_effect=['{"id": 1}{"id": 2}\n', '{"id": 1}\n{"id": 2}\n'],
            ),
            mock.patch.object(common.time, "sleep") as sleep,
        ):
            rows = common.load_jsonl(Path("/tmp/transient.jsonl"))

        self.assertEqual(rows, [{"id": 1}, {"id": 2}])
        sleep.assert_called_once()


class ConfigContractTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_missing_runtime_config_never_falls_back_to_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "config.json"
            with self.assertRaisesRegex(common.ConfigError, "does not exist"):
                common.load_config(missing)

    def test_unknown_fixed_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(
                Path(tmpdir),
                "runtime.json",
                {"supervisor": {"poll_interval_seconds": 30, "typo_seconds": 1}},
            )
            with self.assertRaisesRegex(common.ConfigError, "typo_seconds"):
                common.load_config(path)

    def test_retired_worker_tree_guard_is_dropped_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(
                Path(tmpdir),
                "runtime.json",
                {
                    "worker_tree_guard": {"enabled": True, "mode": "block"},
                    "supervisor": {"poll_interval_seconds": 30},
                },
            )

            loaded = common.load_config(path)

        self.assertNotIn("worker_tree_guard", loaded)
        self.assertEqual(loaded["supervisor"]["poll_interval_seconds"], 30)

    def test_json_comments_are_rejected_in_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime.json"
            path.write_text('{\n  // not runtime JSON\n  "supervisor": {}\n}\n', encoding="utf-8")
            with self.assertRaisesRegex(common.ConfigError, "Unable to parse"):
                common.load_config(path)

    def test_dynamic_agent_name_still_validates_agent_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            valid = self._write(
                root,
                "valid.json",
                {"agents": {"codex_future": {"display_name": "CodexFuture", "provider": "codex"}}},
            )
            invalid = self._write(
                root,
                "invalid.json",
                {"agents": {"codex_future": {"display_name": "CodexFuture", "typo": True}}},
            )

            self.assertEqual(common.load_config(valid)["agents"]["codex_future"]["provider"], "codex")
            with self.assertRaisesRegex(common.ConfigError, "typo"):
                common.load_config(invalid)

    def test_explicit_overlay_is_deep_merged_by_the_canonical_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = self._write(
                root,
                "runtime.json",
                {"supervisor": {"poll_interval_seconds": 30, "stall_after_seconds": 600}},
            )
            overlay = self._write(
                root,
                "runtime.local.json",
                {"supervisor": {"poll_interval_seconds": 60}},
            )

            merged = common.load_config(base, overlay_paths=(overlay,))

        self.assertEqual(merged["supervisor"]["poll_interval_seconds"], 60)
        self.assertEqual(merged["supervisor"]["stall_after_seconds"], 600)

    def test_runtime_config_environment_selects_the_same_config_for_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self._write(
                Path(tmpdir),
                "supervisor-runtime.json",
                {"supervisor": {"poll_interval_seconds": 45}},
            )
            with mock.patch.dict(
                common.os.environ,
                {common.CONFIG_PATH_ENV_VAR: str(runtime)},
                clear=False,
            ):
                loaded = common.load_config()
                worker_env = common.delivery_runtime_env(
                    {"paths": {"status_file": str(Path(tmpdir) / "ai-status.json")}}
                )

        self.assertEqual(loaded["supervisor"]["poll_interval_seconds"], 45)
        self.assertEqual(worker_env[common.CONFIG_PATH_ENV_VAR], str(runtime))


class FailureSummaryTests(unittest.TestCase):
    def test_summarize_failure_reason_treats_claude_credit_balance_as_quota(self) -> None:
        result = common.summarize_failure_reason("Credit balance is too low", "Claude")

        self.assertEqual(result["kind"], "quota")
        self.assertEqual(result["summary"], "Credit balance is too low")

    def test_summarize_failure_reason_treats_github_cli_auth_as_tool_auth(self) -> None:
        result = common.summarize_failure_reason("GitHub CLI is not authenticated. Run gh auth login.", "Claude2")

        self.assertEqual(result["kind"], "tool_auth")
        self.assertEqual(result["summary"], "GitHub CLI auth unavailable")

    def test_summarize_failure_reason_treats_codex_usage_limit_as_quota(self) -> None:
        result = common.summarize_failure_reason(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
            "Codex",
        )

        self.assertEqual(result["kind"], "quota")
        self.assertEqual(result["summary"], "Codex usage limit reached")

    def test_summarize_failure_reason_treats_codex_config_parse_as_provider_config(self) -> None:
        result = common.summarize_failure_reason(
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
            "Codex",
        )

        self.assertEqual(result["kind"], "provider_config")
        self.assertEqual(result["summary"], "Provider config invalid")


class GithubCliEnvTests(unittest.TestCase):
    def test_preserve_github_cli_auth_env_keeps_source_config_when_home_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            gh_config = root / ".config" / "gh"
            gh_config.mkdir(parents=True)
            env = {"HOME": str(root / ".claude2")}

            common.preserve_github_cli_auth_env(env, {"HOME": str(root)})

        self.assertEqual(env["GH_CONFIG_DIR"], str(gh_config))

    def test_preserve_github_cli_auth_env_respects_explicit_config_dir(self) -> None:
        env = {"GH_CONFIG_DIR": "~/custom-gh"}

        common.preserve_github_cli_auth_env(env, {"HOME": "/tmp/ignored"})

        self.assertEqual(env["GH_CONFIG_DIR"], str(Path("~/custom-gh").expanduser()))


class ClaudeAuthTests(unittest.TestCase):
    def test_claude_auth_ready_accepts_long_lived_oauth_token_env(self) -> None:
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test-token"}

        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=None),
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        run_command.assert_not_called()

    def test_claude_auth_ready_refreshes_expired_env_oauth_token(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-old"}
        expired_oauth = {
            "accessToken": "sk-ant-oat01-old",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        refreshed_oauth = {
            "accessToken": "sk-ant-oat01-new",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=refreshed_oauth) as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_called_once_with(env)
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-new")

    def test_claude_auth_ready_prefers_fresh_credentials_over_stale_env_token(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-old"}
        fresh_oauth = {
            "accessToken": "sk-ant-oat01-new",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, fresh_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens") as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_not_called()
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-new")

    def test_claude_auth_ready_accepts_distinct_long_lived_env_token_when_oauth_expired(self) -> None:
        env = {"HOME": "/tmp/test-home", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-long-lived"}
        expired_oauth = {
            "accessToken": "sk-ant-oat01-expired",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        with (
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens") as refresh,
            mock.patch.object(common, "run_command") as run_command,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))

        refresh.assert_not_called()
        run_command.assert_not_called()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-long-lived")

    def test_claude_auth_ready_refreshes_expired_oauth(self) -> None:
        env = {"HOME": "/tmp/test-home"}
        status = mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        expired_oauth = {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
            "scopes": ["user:profile"],
        }
        refreshed_oauth = {
            "accessToken": "new-access",
            "refreshToken": "new-refresh",
            "expiresAt": int(common.time.time() * 1000) + 3_600_000,
            "scopes": ["user:profile", "user:inference"],
        }
        with (
            mock.patch.object(common, "run_command", return_value=status),
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=refreshed_oauth) as refresh,
        ):
            self.assertTrue(common.claude_auth_ready("claude", env=env))
        refresh.assert_called_once_with(env)

    def test_claude_auth_ready_fails_when_refresh_of_expired_oauth_fails(self) -> None:
        status = mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        expired_oauth = {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1,
        }
        with (
            mock.patch.object(common, "run_command", return_value=status),
            mock.patch.object(common, "load_claude_oauth_tokens", return_value=({}, expired_oauth, Path("/tmp/.credentials.json"))),
            mock.patch.object(common, "refresh_claude_oauth_tokens", return_value=None),
        ):
            self.assertFalse(common.claude_auth_ready("claude"))

    def test_refresh_claude_oauth_tokens_updates_credentials_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return json.dumps(
                        {
                            "access_token": "new-access",
                            "refresh_token": "new-refresh",
                            "expires_in": 3600,
                            "scope": "user:profile user:inference",
                        }
                    ).encode("utf-8")

            with mock.patch.object(common.urllib.request, "urlopen", return_value=_Response()):
                refreshed = common.refresh_claude_oauth_tokens({"HOME": tmpdir})

            self.assertIsNotNone(refreshed)
            stored = json.loads(credentials_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["claudeAiOauth"]["accessToken"], "new-access")
            self.assertEqual(stored["claudeAiOauth"]["refreshToken"], "new-refresh")
            self.assertEqual(stored["claudeAiOauth"]["scopes"], ["user:profile", "user:inference"])

    def test_refresh_claude_oauth_tokens_returns_none_on_http_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / ".claude" / ".credentials.json"
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "old-access",
                            "refreshToken": "old-refresh",
                            "expiresAt": 1,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                common.urllib.request,
                "urlopen",
                side_effect=HTTPError(common.CLAUDE_OAUTH_TOKEN_URL, 401, "bad", hdrs=None, fp=None),
            ):
                refreshed = common.refresh_claude_oauth_tokens({"HOME": tmpdir})

            self.assertIsNone(refreshed)


class RecentTaskActivityTests(unittest.TestCase):
    def test_recent_task_activity_reads_from_tail_without_full_log_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            activity_log = root / "ai-activity-log.jsonl"
            lines = []
            for idx in range(40):
                lines.append(json.dumps({"task_id": f"OTHER-{idx}", "message": f"other-{idx}"}))
            for idx in range(8):
                lines.append(json.dumps({"task_id": "TASK-1", "message": f"match-{idx}"}))
            activity_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = common._recent_task_activity({"paths": {"activity_log": str(activity_log)}}, "TASK-1", limit=3)

        self.assertEqual([entry["message"] for entry in result], ["match-5", "match-6", "match-7"])

    def test_recent_task_activity_ignores_partial_tail_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            activity_log = root / "ai-activity-log.jsonl"
            activity_log.write_text(
                "\n".join(
                    [
                        json.dumps({"task_id": "TASK-1", "message": "older"}),
                        json.dumps({"task_id": "TASK-1", "message": "newer"}),
                    ]
                )
                + '\n{"task_id": "TASK-1", "message": "partial"',
                encoding="utf-8",
            )

            result = common._recent_task_activity({"paths": {"activity_log": str(activity_log)}}, "TASK-1", limit=3)

        self.assertEqual([entry["message"] for entry in result], ["older", "newer"])


if __name__ == "__main__":
    unittest.main()


class GithubCliResolutionTests(unittest.TestCase):
    """One rule for "never prefer the broker shim", in one place.

    It used to be written out five times -- task_finalize.sh (bash, still its own),
    check_pr_merge_eligibility.py, apply_branch_protection.py, ai_status.py and
    github_bus.resolve_gh_binary -- and they had drifted: the first four rejected
    the shim while the fifth preferred it, which is how the GitHub bus became the
    only consumer routed through a shim that could not run.
    """

    def test_path_result_wins_when_it_is_not_the_shim(self) -> None:
        with mock.patch.object(common.shutil, "which", return_value="/usr/bin/gh"):
            self.assertEqual(common.resolve_github_cli(), "/usr/bin/gh")

    def test_a_shim_on_path_is_rejected_for_a_system_path(self) -> None:
        shim = f"/anywhere/{common.GH_BROKER_SHIM_SUFFIX}"
        with (
            mock.patch.object(common.shutil, "which", return_value=shim),
            mock.patch.object(common, "SYSTEM_GH_PATHS", ("/usr/bin/gh",)),
            mock.patch.object(common.os, "access", return_value=True),
        ):
            self.assertEqual(common.resolve_github_cli(), "/usr/bin/gh")

    def test_shim_is_the_last_resort_not_the_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shim = root / common.GH_BROKER_SHIM_SUFFIX
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            shim.chmod(0o755)
            with (
                mock.patch.object(common.shutil, "which", return_value=None),
                mock.patch.object(common, "SYSTEM_GH_PATHS", ()),
            ):
                self.assertEqual(common.resolve_github_cli(root), str(shim))

    def test_returns_none_when_nothing_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(common.shutil, "which", return_value=None),
                mock.patch.object(common, "SYSTEM_GH_PATHS", ()),
            ):
                self.assertIsNone(common.resolve_github_cli(Path(tmp)))

    def test_no_python_caller_still_spells_the_rule_itself(self) -> None:
        """Guard the consolidation: a re-added copy should fail here, not drift."""
        repo = Path(common.ROOT)
        callers = [
            repo / "delivery_toolchain/github/check_pr_merge_eligibility.py",
            repo / "delivery_toolchain/github/apply_branch_protection.py",
            repo / "scripts/ai_status.py",
            repo / ".orchestrator/github_bus.py",
        ]
        for path in callers:
            with self.subTest(caller=path.name):
                if not path.is_file():
                    self.skipTest(f"{path} not present")
                body = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    '"/usr/bin/gh", "/usr/local/bin/gh"',
                    body,
                    f"{path.name} re-spells the system-gh fallback; call "
                    "common.resolve_github_cli instead",
                )


class SupervisorProcessIdentityTests(unittest.TestCase):
    """A wrapper that merely names supervisor.py is not the supervisor.

    ``pid_is_supervisor_process`` used to match any argv element ending in
    ``supervisor.py``, so ``timeout 20s python3 .orchestrator/supervisor.py``
    read as a live supervisor to the watchdog while the singleton guard --
    which already applied the strict rule -- refused to treat it as one. The
    two answers now come from one matcher.
    """

    def test_wrapper_process_is_not_read_as_the_supervisor(self) -> None:
        repo = Path(common.ROOT)
        script = repo / ".orchestrator" / "supervisor.py"
        if not script.is_file():
            self.skipTest("supervisor.py not present")
        process = subprocess.Popen(
            ["timeout", "10", "cat", ".orchestrator/supervisor.py"],
            cwd=str(repo),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertFalse(common.pid_is_supervisor_process(process.pid, repo))
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_real_supervisor_argv_still_matches(self) -> None:
        self.assertTrue(
            common.cmdline_is_supervisor_process(["python3", "-u", ".orchestrator/supervisor.py", "--verbose"])
        )
        self.assertTrue(common.cmdline_is_supervisor_process([".orchestrator/supervisor.py", "--once"]))

    def test_supervisor_module_reuses_the_common_matcher(self) -> None:
        import supervisor

        self.assertIs(supervisor.cmdline_is_supervisor_process, common.cmdline_is_supervisor_process)


class TaskArchiveSharesHardenedJsonIoTests(unittest.TestCase):
    """The archive used to carry a weaker private copy of the JSON helpers.

    ``common`` imported ``TaskResolver`` from ``task_archive``, so the archive
    sat below ``common`` in the layering and could not import back -- it spelled
    its own ``write_json``/``load_json`` with a plain ``write_text`` and no
    retry. Readers of ``ai-task-archive`` therefore raced a non-atomic writer.
    """

    def setUp(self) -> None:
        import task_archive

        self.task_archive = task_archive
        self.tmp = tempfile.TemporaryDirectory(prefix="pantheon-archive-io-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "task.json"

    def test_archive_read_tolerates_a_trailing_comma(self) -> None:
        self.path.write_text('{"id": "T-1",}', encoding="utf-8")
        self.assertEqual(self.task_archive.load_json(self.path), {"id": "T-1"})

    def test_archive_write_leaves_the_previous_document_on_failure(self) -> None:
        self.path.write_text('{"id": "T-1"}\n', encoding="utf-8")
        with mock.patch("common.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.task_archive.write_json(self.path, {"id": "T-2"})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"id": "T-1"})

    def test_archive_uses_the_canonical_helpers(self) -> None:
        self.assertIs(self.task_archive.write_json, common.write_json)
        self.assertIs(self.task_archive.load_json, common.load_json)


class UtcTimestampParsingTests(unittest.TestCase):
    """One naive-timestamp policy instead of three.

    ``supervisor_watchdog`` read a naive stamp as *local* time, ``github_bus``
    left it naive (so subtracting it from an aware ``now`` raised TypeError)
    and only ``branch_drift_alarms`` assumed UTC. Every writer here emits
    ``Z``, so UTC is the one correct reading.
    """

    def test_naive_timestamp_is_read_as_utc(self) -> None:
        parsed = common.parse_utc_timestamp("2026-08-20T12:00:00")
        self.assertEqual(parsed, datetime(2026, 8, 20, 12, 0, tzinfo=UTC))

    def test_offset_timestamp_is_converted_to_utc(self) -> None:
        parsed = common.parse_utc_timestamp("2026-08-20T20:00:00+08:00")
        self.assertEqual(parsed, datetime(2026, 8, 20, 12, 0, tzinfo=UTC))
        self.assertEqual(parsed.tzinfo, UTC)

    def test_naive_result_can_be_subtracted_from_now(self) -> None:
        # github_bus._parse_iso used to return a naive datetime here, and the
        # caller's `_iso_now_dt() - parsed` raised instead of backing off.
        parsed = common.parse_utc_timestamp("2026-08-20T12:00:00")
        self.assertIsInstance((datetime.now(UTC) - parsed).total_seconds(), float)

    def test_junk_reads_as_no_timestamp_instead_of_raising(self) -> None:
        for value in ("", None, "not-a-date", 17, {}):
            with self.subTest(value=value):
                self.assertIsNone(common.parse_utc_timestamp(value))

    def test_consumers_share_the_canonical_parser(self) -> None:
        import branch_drift_alarms
        import github_bus
        import supervisor_watchdog

        for module in (github_bus, branch_drift_alarms, supervisor_watchdog):
            with self.subTest(module=module.__name__):
                self.assertIs(module.parse_utc_timestamp, common.parse_utc_timestamp)
