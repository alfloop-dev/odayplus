#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Never let orchestration tests inherit a worker's live coordination root.
# ai_status binds its output paths at import time, so isolation must happen
# before importing it.
_ORIGINAL_STATUS_ROOT = os.environ.get("PANTHEON_STATUS_ROOT")
_ORIGINAL_ORCH_STATUS_ROOT = os.environ.get("ORCH_STATUS_ROOT")
_TEST_STATUS_ROOT_HANDLE = tempfile.TemporaryDirectory(prefix="pantheon-supervisor-tests-")
_TEST_STATUS_ROOT = Path(_TEST_STATUS_ROOT_HANDLE.name).resolve()
os.environ["PANTHEON_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)
os.environ["ORCH_STATUS_ROOT"] = str(_TEST_STATUS_ROOT)

import ai_status
import runtime_state
import supervisor


def tearDownModule() -> None:
    if _ORIGINAL_STATUS_ROOT is None:
        os.environ.pop("PANTHEON_STATUS_ROOT", None)
    else:
        os.environ["PANTHEON_STATUS_ROOT"] = _ORIGINAL_STATUS_ROOT
    if _ORIGINAL_ORCH_STATUS_ROOT is None:
        os.environ.pop("ORCH_STATUS_ROOT", None)
    else:
        os.environ["ORCH_STATUS_ROOT"] = _ORIGINAL_ORCH_STATUS_ROOT
    _TEST_STATUS_ROOT_HANDLE.cleanup()


def load_test_config() -> dict[str, Any]:
    # The committed example is the fixture. config.json is gitignored and holds
    # whatever roster the machine currently runs, so reading it would make these
    # assertions depend on the box rather than on the code: a deployment that
    # trims agents out of owner_fallbacks turns reassignment tests red locally
    # while CI -- which has no config.json and bootstraps from the example --
    # stays green, and a machine with a laxer config hides real failures the
    # same way. Point PANTHEON_TEST_CONFIG at a file to opt into another one.
    override = os.environ.get("PANTHEON_TEST_CONFIG", "").strip()
    config_file = Path(override) if override else Path(__file__).with_name("config.example.json")
    config = json.loads(config_file.read_text(encoding="utf-8"))

    # A test config must never retain repository-relative coordination paths:
    # common.config_path() resolves them against the checked-out code root, not
    # PANTHEON_STATUS_ROOT. Rewrite the complete coordination path table to the
    # module-scoped temporary root before any Supervisor helper can persist.
    config.setdefault("ready_dispatcher", {})["disabled_agents"] = []

    isolated_paths: dict[str, str] = {}
    for key, value in (config.get("paths") or {}).items():
        raw_path = Path(str(value))
        relative_path = Path(raw_path.name) if raw_path.is_absolute() else raw_path
        isolated_paths[key] = str((_TEST_STATUS_ROOT / relative_path).resolve())
    config["paths"] = isolated_paths

    watchdog = config.setdefault("watchdog", {})
    watchdog["state_file"] = str((_TEST_STATUS_ROOT / ".orchestrator/watchdog-state.json").resolve())
    watchdog["metrics_file"] = str(
        (_TEST_STATUS_ROOT / ".orchestrator/metrics/supervisor-watchdog.jsonl").resolve()
    )
    config.setdefault("worker_worktrees", {})["root"] = str(
        (_TEST_STATUS_ROOT / "worker-worktrees").resolve()
    )
    config.setdefault("permission_broker", {})["allowed_workspace_roots"] = [
        str((_TEST_STATUS_ROOT / "workspace").resolve())
    ]
    return config


class TestConfigFixtureTests(unittest.TestCase):
    """The fixture must be the committed example, never the machine's config.

    config.json is gitignored and tracks whatever roster is deployed, so reading
    it makes these tests assert on the box instead of the code -- green on CI,
    red on a machine whose owner_fallbacks were trimmed, and silently permissive
    on a machine whose config is laxer than the example.
    """

    def _example(self) -> dict[str, Any]:
        return json.loads(
            (Path(supervisor.__file__).with_name("config.example.json")).read_text(encoding="utf-8")
        )

    def test_fixture_roster_matches_committed_example(self) -> None:
        config = load_test_config()
        example = self._example()

        self.assertEqual(sorted(config.get("agents") or {}), sorted(example.get("agents") or {}))
        for table in ("owner_fallbacks", "reviewer_fallbacks"):
            with self.subTest(table=table):
                self.assertEqual(
                    (config.get("worker_reassignment") or {}).get(table),
                    (example.get("worker_reassignment") or {}).get(table),
                )

    def test_explicit_override_is_honoured(self) -> None:
        example = self._example()
        example["agents"] = {"solo": example["agents"][next(iter(example["agents"]))]}
        with tempfile.TemporaryDirectory(prefix="pantheon-test-config-") as tmp:
            override = Path(tmp) / "config.json"
            override.write_text(json.dumps(example), encoding="utf-8")
            with mock.patch.dict(os.environ, {"PANTHEON_TEST_CONFIG": str(override)}):
                config = load_test_config()

        self.assertEqual(sorted(config.get("agents") or {}), ["solo"])


class RuntimeConfigTests(unittest.TestCase):
    def test_supervisor_pins_ai_status_to_immutable_runtime(self) -> None:
        self.assertEqual(
            Path(supervisor.runtime_ai_status.__file__).resolve(),
            (SCRIPTS_DIR / "ai_status.py").resolve(),
        )

    def test_dashboard_refresh_does_not_prepend_status_root_scripts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pantheon-stale-status-root-") as tmp:
            status_root = Path(tmp)
            stale_scripts = status_root / "scripts"
            stale_scripts.mkdir()
            (stale_scripts / "ai_status.py").write_text(
                "raise RuntimeError('stale status-root ai_status imported')\n",
                encoding="utf-8",
            )
            config = {"paths": {"status_file": str(status_root / "ai-status.json")}}

            with mock.patch.object(supervisor.runtime_ai_status, "load_state", return_value={}), \
                 mock.patch.object(supervisor.runtime_ai_status, "write_dashboard_bundle"), \
                 mock.patch.object(supervisor.runtime_ai_status, "sync_docs_site"):
                supervisor.refresh_dashboard_runtime_artifacts(config)

            self.assertNotIn(str(stale_scripts), sys.path)

    def test_task_head_resolution_ignores_poisoned_ai_status_module(self) -> None:
        stale_module = mock.Mock()
        stale_module.resolve_task_sha.side_effect = TypeError(
            "resolve_task_sha() got an unexpected keyword argument 'force_refresh'"
        )
        with mock.patch.dict(sys.modules, {"ai_status": stale_module}), \
             mock.patch.object(
                 supervisor.runtime_ai_status,
                 "resolve_task_sha",
                 return_value="1111111122222222333333334444444455555555",
             ) as pinned_resolver, \
             mock.patch.object(
                 supervisor.runtime_ai_status,
                 "task_pr_ci_status",
                 return_value=("OPEN", "success"),
             ):
            self.assertEqual(
                supervisor.resolve_task_progress_head("PINNED-RUNTIME-001"),
                "1111111122222222333333334444444455555555",
            )
            self.assertEqual(
                supervisor.dispatch_priority_for_task(
                    load_test_config(),
                    {
                        "id": "PINNED-RUNTIME-001",
                        "owner": "Codex2",
                        "reviewer": "Codex",
                        "status": "review_approved",
                        "approved_head": "1111111122222222333333334444444455555555",
                    },
                    "Codex2",
                ),
                1,
            )

        self.assertEqual(
            pinned_resolver.call_args_list,
            [
                mock.call("PINNED-RUNTIME-001"),
                mock.call("PINNED-RUNTIME-001", force_refresh=True),
            ],
        )
        stale_module.resolve_task_sha.assert_not_called()

    def test_test_config_coordination_paths_are_temporary_and_absolute(self) -> None:
        config = load_test_config()

        for key, value in config["paths"].items():
            with self.subTest(path=key):
                path = Path(value)
                self.assertTrue(path.is_absolute())
                self.assertTrue(path.is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["watchdog"]["state_file"]).is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["watchdog"]["metrics_file"]).is_relative_to(_TEST_STATUS_ROOT))
        self.assertTrue(Path(config["worker_worktrees"]["root"]).is_relative_to(_TEST_STATUS_ROOT))

    def test_codex_pair_shares_one_account_quota_group(self) -> None:
        config = load_test_config()

        ready_dispatcher = config["ready_dispatcher"]
        quota_caps = ready_dispatcher["max_concurrent_per_quota_group"]

        self.assertNotIn("max_tasks_per_agent", ready_dispatcher)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex"], 4)
        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Codex2"], 4)
        # codex and codex2 reuse the same Codex account -> one shared quota group "codex".
        self.assertEqual(quota_caps["codex"], 6)
        self.assertEqual(supervisor.agent_quota_group_id(config, "codex"), "codex")
        self.assertEqual(supervisor.agent_quota_group_id(config, "codex2"), "codex")
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex"), 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "codex2"), 4)

    def test_claude_concurrency_is_explicitly_capped(self) -> None:
        config = load_test_config()

        ready_dispatcher = config["ready_dispatcher"]

        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude"], 3)
        self.assertEqual(ready_dispatcher["max_concurrent_per_quota_group"]["claude"], 4)
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "claude"), 3)

    def test_claude2_shares_claude_account_quota_group(self) -> None:
        config = load_test_config()

        ready_dispatcher = config["ready_dispatcher"]
        quota_caps = ready_dispatcher["max_concurrent_per_quota_group"]

        self.assertEqual(ready_dispatcher["max_tasks_per_agent_by_agent"]["Claude2"], 3)
        # claude2 reuses the Claude account -> same quota group, no separate claude2 cap.
        self.assertNotIn("claude2", quota_caps)
        self.assertEqual(supervisor.agent_quota_group_id(config, "claude2"), "claude")
        self.assertEqual(supervisor.agent_dispatch_capacity(config, "claude2"), 3)


class DetectWorkerFailureTests(unittest.TestCase):
    def _worker_for_log(self, content: str) -> dict[str, str]:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(content)
        handle.flush()
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return {"log_path": handle.name}

    def test_ignores_error_markers_inside_captured_log_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    "I am reading ai-activity-log.jsonl for context.",
                    '262-{"ts": "2026-04-05T13:36:01Z", "message": "Error: Model \\"grok-code-fast-1\\" from --model flag is not available."}',
                    'worker_retry_scheduled: {"message": "Transient worker failure detected; retry 1 scheduled at 2026-04-05T13:48:48Z: reason: \\"QUOTA_EXHAUSTED\\""}',
                    "No local failure happened in this session.",
                ]
            )
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_missing_provider_cli_from_wrapper_message(self) -> None:
        """The 2026-08-05 Codex outage: this exact line sat in 194 worker logs.

        It matched no failure pattern, so `detect_worker_failure` returned None
        and every one of those dispatches was recorded as an unexplained exit
        with nothing printed. The lane was dead for six hours and the console
        showed only task reassignments.
        """

        worker = self._worker_for_log(
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
        )

    def test_detects_missing_cli_for_every_provider_wrapper(self) -> None:
        for message in (
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
            "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
            "Claude CLI binary not found under ~/.vscode-server/extensions.",
            "Copilot CLI binary not found under ~/.local/share/pantheon-orchestrator-tools.",
            "GitHub CLI binary not found under ~/.local/share/pantheon-orchestrator-tools.",
        ):
            with self.subTest(message=message):
                worker = self._worker_for_log(message + "\n")
                self.assertEqual(supervisor.detect_worker_failure(worker), message)

    def test_a_non_provider_binary_not_found_line_is_not_a_lane_failure(self) -> None:
        """Ordinary build output must not pause a healthy lane for 900s.

        An earlier pattern matched any line-initial "<token> binary not found",
        so a toolchain message like "protoc binary not found" read as a dead
        provider CLI.
        """

        for line in (
            "protoc binary not found in PATH",
            "ffmpeg binary not found",
            "terraform binary not found under /usr/local/bin",
        ):
            with self.subTest(line=line):
                worker = self._worker_for_log(line + "\n")
                self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_another_providers_launcher_error_does_not_kill_this_lane(self) -> None:
        """A codex worker reporting Claude's launcher error says nothing about codex."""

        reason = "Claude CLI binary not found under ~/.vscode-server/extensions."

        own = supervisor.classify_worker_failure({}, {"provider": "claude2"}, reason)
        other = supervisor.classify_worker_failure({}, {"provider": "codex3"}, reason)

        self.assertEqual(own["kind"], "provider_unavailable")
        self.assertNotEqual(other["kind"], "provider_unavailable")

    def test_gemini_launcher_error_maps_to_the_antigravity_family(self) -> None:
        failure = supervisor.classify_worker_failure(
            {},
            {"provider": "antigravity5"},
            "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
        )
        self.assertEqual(failure["kind"], "provider_unavailable")

    def test_missing_cli_wording_inside_task_output_is_not_a_lane_failure(self) -> None:
        """Ordinary work that mentions the wording must not pause a live lane."""

        worker = self._worker_for_log(
            "\n".join(
                [
                    "codex",
                    '+    raise RuntimeError("Codex CLI binary not found")',
                    'assert "CLI binary not found" in caplog.text',
                    "All tests passed.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_real_model_availability_failure(self) -> None:
        worker = self._worker_for_log('Error: Model "grok-code-fast-1" from --model flag is not available.\n')

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            'Error: Model "grok-code-fast-1" from --model flag is not available.',
        )

    def test_detects_real_gemini_quota_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                    "retryDelayMs: 1807388.816191,",
                    "reason: 'QUOTA_EXHAUSTED'",
                    "An unexpected critical error occurred:[object Object]",
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

    def test_detects_claude_auth_failure_from_cli_log(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    '{"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,"retry_delay_ms":590.5,"error_status":401,"error":"authentication_failed"}',
                    '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
                ]
            )
            + "\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Failed to authenticate. API Error: 401 {\\"type\\":\\"error\\",\\"error\\":{\\"type\\":\\"authentication_error\\",\\"message\\":\\"Invalid authentication credentials\\"}}"}]}}',
        )

    def test_ignores_auth_text_inside_tool_result_user_message(self) -> None:
        worker = self._worker_for_log(
            '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"prior state said not authenticated, but this is just captured inspection output"}]}}\n'
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_transcribed_limit_error_inside_review_notes(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "Reviewer note:",
                    'Auto-reassigned ownership from Claude to Copilot after repeated provider failure: {"type":"result","result":"You\'ve hit your limit · resets 12am (Asia/Taipei)","worker_run_id":"claude-123"}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_search_result_json_field_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '718:      "next": "Auto-reassigned ownership from Copilot to Codex after repeated Copilot capacity/429: 402 You have no quota",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_activity_log_bullet_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            "- 2026-05-09T07:29:01Z · Orchestrator · task_reassigned · Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_captured_queue_event_json_that_mentions_prior_quota_reassignment(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "event_key": "dispatcher:Codex2:BFF-LUV-SEM-001",
                    "target_agent": "codex2",
                    "message": "Wake-up queued for supervisor: review_ready_dispatch",
                    "metadata": {
                        "task": {
                            "next": "Auto-reassigned review from Copilot to Codex2 after repeated Copilot quota terminal: 402 You have no quota"
                        }
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_allowed_rate_limit_event(self) -> None:
        worker = self._worker_for_log(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "allowed",
                        "resetsAt": 1778324400,
                        "rateLimitType": "five_hour",
                        "overageStatus": "rejected",
                        "overageDisabledReason": "org_level_disabled",
                        "isUsingOverage": False,
                    },
                }
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_non_allowed_rate_limit_event(self) -> None:
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rate_limited",
                    "rateLimitType": "five_hour",
                },
            }
        )
        worker = self._worker_for_log(line + "\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), line)

    def test_detects_real_no_quota_line(self) -> None:
        worker = self._worker_for_log("402 You have no quota\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "402 You have no quota")

    def test_ignores_git_fatal_from_tool_command_output(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    "/bin/bash -lc 'git show abc:missing.md' in /repo",
                    " exited 128 in 0ms:",
                    "fatal: path 'missing.md' does not exist in 'abc'",
                    "worker continued reviewing after this probe.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_detects_standalone_fatal_line(self) -> None:
        worker = self._worker_for_log("fatal: provider process crashed\n")

        self.assertEqual(supervisor.detect_worker_failure(worker), "fatal: provider process crashed")

    def test_ignores_log_search_result_json_that_mentions_quota(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "exec",
                    '.orchestrator/logs/20260417T134622225365Z-claude.log:24:{"type":"user","message":{"content":"402 You have no quota"}}',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_pretty_json_field_that_mentions_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "succeeded in 252ms:",
                    '"next": "Auto-reassigned ownership from Gemini2 after repeated Gemini2 auth: not authenticated",',
                    "No local failure happened in this session.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_ignores_diff_assignment_that_quotes_auth_failure(self) -> None:
        worker = self._worker_for_log(
            "\n".join(
                [
                    "**Blocker**",
                    '+ completed.stderr = b"Error: not authenticated, please login first"',
                    "The quoted failure came from a reviewed diff, not this worker process.",
                ]
            )
            + "\n"
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_auto_reassigned_log_summary_quoted_auth_ignored(self) -> None:
        """Verify auto-reassigned notes quoting auth or permission errors are not misclassified as live worker failures."""
        worker = self._worker_for_log(
            "next: Auto-reassigned ownership from Gemini2 after repeated Gemini2 auth: not authenticated\n"
        )
        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_python_source_assignment_quoted_auth_ignored(self) -> None:
        worker = self._worker_for_log(
            'reason = f"Principal {attempt.actor_id!r} is not authenticated"\n'
        )

        self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_classifies_gemini_capacity_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 429 RESOURCE_EXHAUSTED")

        self.assertEqual(result["kind"], "capacity_retryable")
        self.assertTrue(result["transient"])

    def test_classifies_gemini_terminal_quota_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_copilot_no_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "copilot"}

        result = supervisor.classify_worker_failure(config, worker, "402 You have no quota")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_claude_credit_balance_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude"}

        result = supervisor.classify_worker_failure(config, worker, "Credit balance is too low")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_qwen_free_tier_quota_failure_as_terminal(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "qwen"}

        result = supervisor.classify_worker_failure(config, worker, "[API Error: Qwen OAuth free tier quota exceeded.]")

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_classifies_codex_usage_limit_failure_as_terminal_quota(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

        self.assertEqual(result["kind"], "quota_terminal")
        self.assertFalse(result["transient"])

    def test_detects_codex_usage_limit_line_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
        )

    def test_detects_codex_config_parse_failure_as_worker_failure(self) -> None:
        worker = self._worker_for_log(
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`\n"
        )

        self.assertEqual(
            supervisor.detect_worker_failure(worker),
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

    def test_classifies_gemini_auth_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "status: 401 unauthorized")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_not_authenticated_failure_as_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "Claude CLI is not authenticated; inbox fallback is disabled.")

        self.assertEqual(result["kind"], "auth")
        self.assertFalse(result["transient"])

    def test_classifies_github_cli_auth_failure_as_tool_auth(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "claude2"}

        result = supervisor.classify_worker_failure(config, worker, "GitHub CLI is not authenticated. Run gh auth login.")

        self.assertEqual(result["kind"], "tool_auth")
        self.assertFalse(result["transient"])

    def test_classifies_codex_config_parse_failure_as_provider_config(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "codex1-1"}

        result = supervisor.classify_worker_failure(
            config,
            worker,
            "Error loading config.toml: unknown variant `priority`, expected `fast` or `flex` in `service_tier`",
        )

        self.assertEqual(result["kind"], "provider_config")
        self.assertFalse(result["transient"])

    def test_auth_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("auth"))

    def test_provider_config_failures_pause_provider_dispatch(self) -> None:
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind("provider_config"))

    def test_tool_auth_failures_do_not_pause_provider_dispatch(self) -> None:
        self.assertFalse(supervisor.should_pause_dispatch_for_failure_kind("tool_auth"))

    def test_classifies_gemini_unknown_critical_failure(self) -> None:
        config = {"worker_retry": {"transient_error_patterns": ["429", "resource_exhausted", "rate limit"]}}
        worker = {"provider": "gemini"}

        result = supervisor.classify_worker_failure(config, worker, "An unexpected critical error occurred:[object Object]")

        self.assertEqual(result["kind"], "unknown_critical")
        self.assertFalse(result["transient"])

    def test_formats_runtime_timestamp_in_taipei_time(self) -> None:
        self.assertEqual(
            supervisor.format_runtime_timestamp_local("2026-04-06T14:35:42Z"),
            "2026-04-06 22:35:42",
        )

    @mock.patch("supervisor.os.kill")
    @mock.patch("supervisor.os.waitpid", return_value=(43210, 0))
    def test_pid_is_alive_treats_reaped_child_as_dead(self, _waitpid: mock.Mock, _kill: mock.Mock) -> None:
        self.assertFalse(supervisor.pid_is_alive(43210))

    def test_parse_quota_retry_hint_codex_pm(self) -> None:
        from datetime import datetime

        # 03:05Z on 2026-04-28 = 11:05 LOCAL (Asia/Taipei). "7:00 PM" in local
        # time = 19:00 LOCAL = 11:00 UTC same day.
        now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 28, 11, 0, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_rolls_to_next_day_when_past(self) -> None:
        from datetime import datetime

        # 06:00Z on 2026-04-28 = 14:00 LOCAL same day (Asia/Taipei). "1pm" = 13:00
        # LOCAL is already past, so the hint should roll forward to the next day:
        # 2026-04-29 13:00 LOCAL = 2026-04-29 05:00 UTC.
        now = datetime(2026, 4, 28, 6, 0, 0, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 1pm (Asia/Taipei)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 4, 29, 5, 0, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_honors_explicit_utc(self) -> None:
        from datetime import datetime

        now = datetime(2026, 5, 8, 16, 53, 27, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "You've hit your limit · resets 8:40pm (UTC)",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 8, 20, 40, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_codex_full_date(self) -> None:
        from datetime import datetime

        now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=UTC)
        hint = supervisor.parse_quota_retry_hint(
            "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
            "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
            now=now,
        )

        self.assertEqual(hint, datetime(2026, 5, 18, 16, 40, 0, tzinfo=UTC))

    def test_parse_quota_retry_hint_returns_none_when_absent(self) -> None:
        self.assertIsNone(supervisor.parse_quota_retry_hint("Credit balance is too low"))
        self.assertIsNone(supervisor.parse_quota_retry_hint(None))

    def test_missing_provider_cli_classifies_as_provider_unavailable(self) -> None:
        failure = supervisor.classify_worker_failure(
            {},
            {"provider": "codex"},
            "Codex CLI binary not found at /home/lupin/.npm-global/bin/codex or on PATH.",
        )

        self.assertEqual(failure["kind"], "provider_unavailable")
        self.assertFalse(failure["transient"])
        self.assertTrue(supervisor.should_pause_dispatch_for_failure_kind(failure["kind"]))

    # Rotation-enabled, as the seven antigravity providers in the live config
    # are. Asserting no-rotation against a provider that cannot rotate passes
    # whatever the code does, which is how the first version of this test missed
    # the defect it was written to catch.
    ROTATING_CONFIG = {
        "provider_guardrails": {
            "capacity_pause_seconds": 900,
            "quota_terminal_pause_seconds": 900,
        },
        "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        "providers": {
            "antigravity5": {
                "antigravity": {
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "Claude Sonnet 4.6 (Thinking)",
                    }
                }
            }
        },
    }

    def test_missing_provider_cli_pauses_dispatch_without_rotating_models(self) -> None:
        """A dead binary has no second model pool to fall back onto.

        Rotation answers "this model pool is exhausted" by dispatching on the
        other pool. When the binary itself is gone, neither pool is reachable,
        so rotating just resumes the sub-second failure loop.
        """

        state: dict = {}
        self.assertTrue(
            supervisor.model_rotation.rotation_enabled(self.ROTATING_CONFIG, "antigravity5"),
            "fixture must have rotation enabled or this test proves nothing",
        )

        with (
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "record_exhaustion") as rotate,
        ):
            paused = supervisor.mark_provider_dispatch_paused(
                self.ROTATING_CONFIG,
                state,
                "antigravity5",
                "Antigravity CLI (agy) binary not found under ~/.local/bin or PATH.",
                task_id="ODP-ORCH-EXAMPLE-001",
                worker_run_id="agy-run-1",
                failure_kind="provider_unavailable",
                pause_kind="provider_unavailable",
            )

        self.assertTrue(paused)
        rotate.assert_not_called()
        entry = state["provider_guardrails"]["dispatch_pauses"]["antigravity5"]
        self.assertEqual(entry["pause_kind"], "provider_unavailable")
        # Finite, so reinstalling the CLI brings the lane back without manual
        # intervention -- and so a lane nobody fixes keeps re-announcing itself.
        self.assertGreaterEqual(entry["reset_after_seconds"], 60)
        self.assertTrue(entry["blocked_until"])

    def test_quota_on_the_same_provider_still_rotates(self) -> None:
        """Guard the other direction: the exclusion must not disable rotation."""

        state: dict = {}
        with (
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "record_exhaustion", return_value={"exhausted_pool": "gemini"}) as rotate,
        ):
            supervisor.mark_provider_dispatch_paused(
                self.ROTATING_CONFIG,
                state,
                "antigravity5",
                "Error: Individual quota reached. Please upgrade your subscription. Resets in 2h21m32s.",
                task_id="ODP-ORCH-EXAMPLE-001",
                worker_run_id="agy-run-2",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        rotate.assert_called_once()

    def test_provider_unavailable_pause_seconds_has_a_default(self) -> None:
        settings = supervisor.provider_guardrail_settings({})
        self.assertGreaterEqual(int(settings["provider_unavailable_pause_seconds"]), 60)

    def test_mark_provider_dispatch_paused_honors_codex_retry_at(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 7:00 PM.",
                task_id="SD-FND-003",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex"]
        # 7pm Asia/Taipei = 11:00 UTC same day, far longer than the default 900s
        self.assertEqual(entry["blocked_until"], "2026-04-28T11:00:00Z")
        self.assertEqual(entry["pause_kind"], "quota_terminal")
        # reset_after_seconds should reflect the actual hint window, not the default
        self.assertGreater(entry["reset_after_seconds"], 900)
        self.assertEqual(entry["reset_after_seconds"], int((11 - 3) * 3600 - 5 * 60))

    def test_mark_provider_dispatch_paused_honors_codex_full_date_retry_at(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 16, 10, 5, 36, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="TRN-002",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["trigger_provider"], "codex2_3")
        self.assertEqual(entry["blocked_until"], "2026-05-18T16:40:00Z")
        self.assertEqual(entry["reset_after_seconds"], 196464)

    def test_mark_provider_dispatch_paused_caps_codex_retry_hint_when_configured(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {
                "capacity_pause_seconds": 900,
                "quota_terminal_pause_seconds": 900,
                "quota_terminal_hint_max_seconds": 3600,
            },
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"quota_group": "codex2"}},
        }
        state: dict = {}

        fake_now = datetime(2026, 5, 17, 20, 2, 2, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex2-3",
                "ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
                "to purchase more credits or try again at May 19th, 2026 12:40 AM.",
                task_id="OODA-E2E-005",
                worker_run_id="codex-run-1",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["codex2"]
        self.assertEqual(entry["blocked_until"], "2026-05-17T21:02:02Z")
        self.assertEqual(entry["hint_blocked_until"], "2026-05-18T16:40:00Z")
        self.assertTrue(entry["hint_capped"])
        self.assertEqual(entry["reset_after_seconds"], 3600)

    def test_mark_provider_dispatch_paused_uses_default_when_no_hint(self) -> None:
        from datetime import datetime

        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state: dict = {}

        fake_now = datetime(2026, 4, 28, 3, 5, 0, tzinfo=UTC)
        with (
            mock.patch.object(supervisor, "datetime") as datetime_mock,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            datetime_mock.now.return_value = fake_now
            datetime_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "claude",
                "Credit balance is too low",
                failure_kind="quota_terminal",
                pause_kind="quota_terminal",
            )

        entry = state["provider_guardrails"]["dispatch_pauses"]["claude"]
        # 03:05Z + 900s = 03:20Z
        self.assertEqual(entry["blocked_until"], "2026-04-28T03:20:00Z")
        self.assertEqual(entry["reset_after_seconds"], 900)

    def test_codex_slot_pause_uses_shared_quota_group(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state: dict = {}

        with mock.patch.object(supervisor, "write_activity_log"):
            supervisor.mark_provider_dispatch_paused(
                config,
                state,
                "codex1-1",
                "status: 429 RESOURCE_EXHAUSTED",
                failure_kind="capacity_retryable",
                pause_kind="capacity_retryable",
            )

        pauses = state["provider_guardrails"]["dispatch_pauses"]
        self.assertIn("codex1", pauses)
        self.assertNotIn("codex1_1", pauses)
        self.assertEqual(pauses["codex1"]["trigger_provider"], "codex1_1")
        self.assertIs(supervisor.current_provider_dispatch_pause(state, "codex1-2", config), pauses["codex1"])

    def test_expire_provider_dispatch_pauses_removes_expired_entry(self) -> None:
        config = {
            "provider_guardrails": {"capacity_pause_seconds": 900, "quota_terminal_pause_seconds": 900},
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "copilot": {
                        "provider": "copilot",
                        "blocked_until": "2026-04-06T12:00:00Z",
                        "pause_kind": "quota_terminal",
                        "task_id": "PKT-001",
                        "worker_run_id": "copilot-run",
                        "raw_ref": ".orchestrator/evidence/copilot.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")

    def test_expire_provider_dispatch_pauses_clears_quota_pause_after_account_switch(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex": {"quota_group": "codex"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "trigger_provider": "codex",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "pause_kind": "quota_terminal",
                        "auth_identity_hash": "old-account",
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="new-account"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.expire_provider_dispatch_pauses(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        message = write_activity_log.call_args.args[1]["message"]
        self.assertIn("account identity changed", message)

    def test_clear_provider_dispatch_pause_removes_group_pause(self) -> None:
        config = {
            "paths": {"activity_log": "/tmp/test-activity-log.jsonl"},
            "providers": {"codex2-3": {"delivery_mode": "codex", "quota_group": "codex2"}},
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex2": {
                        "task_id": "OODA-E2E-005",
                        "worker_run_id": "codex-run-1",
                        "raw_ref": ".orchestrator/evidence/codex.json",
                    }
                }
            }
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.clear_provider_dispatch_pause(config, state, "codex2-3")

        self.assertTrue(changed)
        self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "provider_dispatch_resumed")
        self.assertEqual(write_activity_log.call_args.args[1]["provider"], "codex2")


class ProcessQueueDispatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {
                    "id": "codex",
                    "name": "Codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                }
            },
            "providers": {
                "codex": {
                    "delivery_mode": "codex",
                }
            },
        }
        self.provider_report: dict[str, object] = {}

    def test_worker_tree_guard_warns_without_blocking(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "warn",
                "blocking_globs": [".orchestrator/skills/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[{"status": " M", "path": ".orchestrator/skills/worker-anchor-commit.md"}],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertTrue(ok)
        self.assertIn("anchor or close out", message or "")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_dirty_tree_warning")

    def test_worker_tree_guard_blocks_in_block_mode(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "block",
                "blocking_globs": ["docs/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[{"status": " M", "path": "docs/conventions/GIT_WORKFLOW.md"}],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertFalse(ok)
        self.assertIn("docs/conventions/GIT_WORKFLOW.md", message or "")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_blocked_dirty_tree")

    def test_worker_tree_guard_ignores_runtime_state_only(self) -> None:
        config = {
            **self.config,
            "worker_tree_guard": {
                "enabled": True,
                "mode": "block",
                "blocking_globs": [".orchestrator/skills/**"],
                "auto_restore_globs": ["ai-status.json", "docs-site/**"],
            },
        }

        with (
            mock.patch.object(
                supervisor,
                "_git_dirty_entries",
                return_value=[
                    {"status": " M", "path": "ai-status.json"},
                    {"status": " M", "path": "docs-site/current-work.md"},
                ],
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            ok, message = supervisor.check_worker_tree_clean(
                config,
                run_id="evt-1",
                task_id="OPS-WORKER-ANCHOR-001",
                target_agent="Codex",
                queue_event_id="evt-1",
            )

        self.assertTrue(ok)
        self.assertIsNone(message)
        write_activity_log.assert_not_called()

    def test_prepare_worker_workspace_allocates_task_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-1",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "ops-worktree-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        self.assertEqual(request.metadata["workspace_branch"], "task/OPS-WORKTREE-001")
        self.assertEqual(request.metadata["status_root"], str(repo_root.resolve()))
        self.assertEqual(state["worker_worktrees"]["leases"]["OPS-WORKTREE-001"]["path"], str(expected_path))
        create_worktree.assert_called_once_with(repo_root.resolve(), expected_path, "task/OPS-WORKTREE-001", "origin/dev")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_worktree_allocated")

    def test_review_dispatch_uses_task_branch_not_mainline_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="review exact task head",
                task_id="ODP-PLAN-REVIEW-001",
                reason="review_ready_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(
                    supervisor,
                    "_create_worker_worktree",
                    return_value=(True, None),
                ) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-review",
                    target_agent="Codex",
                )

        expected_path = worktree_root / "pantheon" / "odp-plan-review-001"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_branch"], "task/ODP-PLAN-REVIEW-001")
        self.assertNotEqual(request.metadata["workspace_branch"], "dev")
        self.assertNotEqual(request.metadata["workspace_branch"], "main")
        create_worktree.assert_called_once_with(
            repo_root.resolve(),
            expected_path,
            "task/ODP-PLAN-REVIEW-001",
            "origin/dev",
        )

    def test_prepare_worker_workspace_allocates_chair_review_worktree_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                    "execution_reasons": ["chair_review:*"],
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=None,
                reason="chair_review:operational_review",
                metadata={"workspace_task_id": "chair-review-20260531-153804-codex2"},
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)) as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-chair",
                    target_agent="Codex2",
                )

        expected_path = worktree_root / "pantheon" / "chair-review-20260531-153804-codex2"
        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(request.metadata["workspace_mode"], "isolated_worktree")
        self.assertEqual(request.metadata["workspace_path"], str(expected_path))
        self.assertEqual(request.metadata["workspace_branch"], "task/chair-review-20260531-153804-codex2")
        self.assertIsNone(state["worker_worktrees"]["leases"]["chair-review-20260531-153804-codex2"]["task_id"])
        create_worktree.assert_called_once_with(
            repo_root.resolve(),
            expected_path,
            "task/chair-review-20260531-153804-codex2",
            "origin/dev",
        )
        self.assertEqual(write_activity_log.call_args.args[1]["workspace_task_id"], "chair-review-20260531-153804-codex2")

    def test_prepare_worker_workspace_materializes_task_brief_into_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            source_brief = repo_root / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            source_brief.parent.mkdir(parents=True)
            source_brief.write_text("# Source brief\n", encoding="utf-8")
            worktree_root = Path(tmpdir) / "workers"
            config = {
                **self.config,
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "activity-log.jsonl"),
                },
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(worktree_root),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-BRIEF-001",
                reason="owned_in_progress_dispatch",
                context_files=[".orchestrator/task-briefs/ops_brief_001.md"],
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=None),
                mock.patch.object(supervisor, "_branch_checked_out_in_root", return_value=False),
                mock.patch.object(supervisor, "_create_worker_worktree", return_value=(True, None)),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-brief",
                    target_agent="Codex",
                )

            self.assertTrue(ok)
            self.assertIsNone(message)
            copied_brief = Path(request.metadata["workspace_path"]) / ".orchestrator" / "task-briefs" / "ops_brief_001.md"
            self.assertEqual(copied_brief.read_text(encoding="utf-8"), "# Source brief\n")
            self.assertEqual(request.metadata["materialized_context_files"], [".orchestrator/task-briefs/ops_brief_001.md"])

    def test_prepare_worker_workspace_blocks_dirty_reused_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "skipped_dirty_worktree"),
                ) as refresh_worktree,
                mock.patch.object(
                    supervisor,
                    "_fetch_authoritative_task_head",
                    return_value=("a" * 40, "local_only_task_ref"),
                ),
                mock.patch.object(supervisor, "_quarantine_and_preserve_dirty_worktree", return_value=False) as reset_worktree,
                mock.patch.object(supervisor, "_create_worker_worktree") as create_worktree,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-dirty",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        assert message is not None
        self.assertIn("dirty tracked or staged changes", message)
        self.assertNotIn("workspace_path", request.metadata)
        self.assertNotIn("worker_worktrees", state)
        refresh_worktree.assert_called_once()
        reset_worktree.assert_called_once()
        create_worktree.assert_not_called()
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "dispatch_blocked_worktree_lease"],
        )
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["refresh_status"], "skipped_dirty_worktree")

    def test_prepare_worker_workspace_recovers_dirty_worktree_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-RECOVER-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "skipped_dirty_worktree"),
                ) as refresh_worktree,
                mock.patch.object(
                    supervisor,
                    "_fetch_authoritative_task_head",
                    return_value=("a" * 40, "local_only_task_ref"),
                ),
                mock.patch.object(supervisor, "_quarantine_and_preserve_dirty_worktree", return_value=True) as reset_worktree,
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-recover",
                    target_agent="Codex",
                )

        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertEqual(refresh_worktree.call_count, 1)
        reset_worktree.assert_called_once()
        self.assertEqual(
            [call.args[1]["type"] for call in write_activity_log.call_args_list],
            ["worker_worktree_refreshed", "worker_worktree_lease_recovered", "worker_worktree_reused"],
        )


    def test_prepare_worker_workspace_blocks_every_other_unsafe_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-unsafe-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="OPS-WORKTREE-UNSAFE-001",
                reason="owned_in_progress_dispatch",
            )

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(False, "task_head_mismatch: local=a remote=b"),
                ),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    {},
                    request,
                    queue_event_id="evt-unsafe",
                    target_agent="Codex",
                )

        self.assertFalse(ok)
        self.assertIn("fail-closed refresh policy", message or "")
        self.assertEqual(write_activity_log.call_args_list[-1].args[1]["type"], "dispatch_blocked_worktree_lease")

    def test_prepare_worker_workspace_prompts_owner_to_advance_diverged_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            repo_root.mkdir()
            worktree_path = Path(tmpdir) / "workers" / "pantheon" / "ops-worktree-rebase-001"
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(Path(tmpdir) / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict[str, object] = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="original owner dispatch",
                task_id="OPS-WORKTREE-REBASE-001",
                reason="owned_in_progress_dispatch",
            )
            refresh_status = "base_advance_rebase_required:local=" + "a" * 40 + ",base=" + "b" * 40

            with (
                mock.patch.object(supervisor, "_existing_worktree_for_branch", return_value=worktree_path),
                mock.patch.object(
                    supervisor,
                    "_refresh_reused_worker_worktree",
                    return_value=(True, refresh_status),
                ),
                mock.patch.object(supervisor, "materialize_worker_context_files", return_value=[]),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                ok, message = supervisor.prepare_worker_workspace(
                    config,
                    state,
                    request,
                    queue_event_id="evt-rebase",
                    target_agent="Codex",
                )

        self.assertTrue(ok)
        self.assertIsNone(message)
        self.assertTrue(request.metadata["base_advance_required"])
        self.assertEqual(request.metadata["worktree_refresh_status"], refresh_status)
        self.assertIn("BASE ADVANCE REQUIRED BEFORE EDITING OR HANDOFF", request.message)
        self.assertIn("must fetch and rebase/compose", request.message)
        self.assertTrue(request.message.endswith("original owner dispatch"))

    def test_failed_queue_finalization_preserves_worker_worktree(self) -> None:
        state = {
            "queue": {"events": {"evt-failed": {"status": "started"}}},
            "workers": {},
        }
        worker = {
            "run_id": "run-failed",
            "queue_event_id": "evt-failed",
            "task_id": "OPS-WORKTREE-PRESERVE-001",
            "workspace_path": "/tmp/worker-preserve",
        }

        with mock.patch.object(supervisor, "reset_worker_worktree_after_failure") as reset_worktree:
            supervisor.finalize_queue_event_record(
                self.config,
                state,
                worker,
                "failed",
                "worker failed with task-owned dirt",
            )

        reset_worktree.assert_not_called()
        record = state["queue"]["events"]["evt-failed"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "worker failed with task-owned dirt")

    def test_process_queue_checks_worker_guard_inside_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "pantheon"
            workspace = Path(tmpdir) / "workers" / "pantheon" / "bus-val-004"
            repo_root.mkdir()
            workspace.mkdir(parents=True)
            config = {
                **self.config,
                "paths": {"status_file": str(repo_root / "ai-status.json")},
                "worker_worktrees": {"enabled": True, "root": str(workspace.parent.parent)},
            }
            current_task = {
                "id": "BUS-VAL-004",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Gemini",
                "depends_on": [],
                "last_update": "2026-04-05T14:54:01Z",
            }
            queue_payload = {
                "event_id": "evt-current",
                "task_id": "BUS-VAL-004",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            state = {"queue": {"events": {}}, "workers": {}}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="BUS-VAL-004",
                reason="owned_in_progress_dispatch",
            )

            def prepare_workspace(_config, _state, prepared_request, **_kwargs):
                prepared_request.metadata.update(
                    {
                        "workspace_path": str(workspace),
                        "workspace_branch": "task/BUS-VAL-004",
                        "workspace_mode": "isolated_worktree",
                        "status_root": str(repo_root.resolve()),
                    }
                )
                return True, None

            with (
                mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
                mock.patch.object(supervisor, "build_request", return_value=request),
                mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
                mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)) as guard,
                mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", {"manual_confirmation_required": False, "auto_delivered": True})),
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            ):
                changed = supervisor.process_queue(config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(guard.call_args.kwargs["cwd"], workspace)

    def test_process_queue_isolates_workspace_exception_and_starts_next_event(self) -> None:
        tasks = [
            {
                "id": task_id,
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Reviewer",
                "depends_on": [],
            }
            for task_id in ("BAD-001", "GOOD-001")
        ]
        events = [
            {
                "event_id": event_id,
                "task_id": task_id,
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            for event_id, task_id in (("evt-bad", "BAD-001"), ("evt-good", "GOOD-001"))
        ]
        state = {"queue": {"events": {}}, "workers": {}}

        def prepare_workspace(_config, _state, request, **_kwargs):
            if request.task_id == "BAD-001":
                raise ValueError("missing required source document")
            request.metadata["workspace_path"] = "/tmp/good-workspace"
            return True, None

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=events),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value="codex"),
            mock.patch.object(supervisor, "prepare_worker_workspace", side_effect=prepare_workspace),
            mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-good", {"manual_confirmation_required": False, "auto_delivered": True}),
            ) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-bad"]["status"], "failed")
        self.assertIn("missing required source document", state["queue"]["events"]["evt-bad"]["error"])
        self.assertEqual(state["queue"]["events"]["evt-good"]["status"], "started")
        start_worker.assert_called_once()
        failure_events = [
            call.args[1]
            for call in write_activity_log.call_args_list
            if call.args[1].get("type") == "wake_failed"
        ]
        self.assertEqual(failure_events[0]["task_id"], "BAD-001")

    def test_process_queue_isolates_request_construction_exception(self) -> None:
        events = [
            {
                "event_id": event_id,
                "task_id": task_id,
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "owned_in_progress_dispatch",
                "message": "wake",
            }
            for event_id, task_id in (("evt-bad", "BAD-REQUEST"), ("evt-good", "GOOD-REQUEST"))
        ]
        tasks = [
            {
                "id": event["task_id"],
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Reviewer",
            }
            for event in events
        ]

        def build_request(_config, event, **_kwargs):
            if event["task_id"] == "BAD-REQUEST":
                raise ValueError("invalid task source metadata")
            return supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="GOOD-REQUEST",
                reason="owned_in_progress_dispatch",
            )

        state = {"queue": {"events": {}}, "workers": {}}
        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=events),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "build_request", side_effect=build_request),
            mock.patch.object(supervisor, "select_dispatch_agent_id", return_value="codex"),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, None)),
            mock.patch.object(
                supervisor,
                "start_worker_for_request",
                return_value=(True, "run-good", {"manual_confirmation_required": False, "auto_delivered": True}),
            ) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-bad"]["status"], "failed")
        self.assertIn("invalid task source metadata", state["queue"]["events"]["evt-bad"]["error"])
        self.assertEqual(state["queue"]["events"]["evt-good"]["status"], "started")
        start_worker.assert_called_once()

    def test_queue_dispatch_event_safely_contains_source_metadata_error(self) -> None:
        event = {"task_id": "BAD-EVENT", "target_agent": "Antigravity"}
        with (
            mock.patch.object(
                supervisor,
                "queue_delivery_event",
                side_effect=ValueError("missing source document"),
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            queued = supervisor.queue_dispatch_event_safely(self.config, event)

        self.assertFalse(queued)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "dispatch_event_rejected")
        self.assertIn("missing source document", write_activity_log.call_args.args[1]["message"])

    def test_build_request_uses_provider_model_preference_for_qwen_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "qwen": {
                    "id": "qwen",
                    "display_name": "Qwen",
                    "provider": "qwen",
                    "adapter": "qwen",
                }
            },
            "providers": {
                "qwen": {
                    "delivery_mode": "qwen",
                    "model_preference": {
                        "qwen": "qwen3-coder-plus",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "qwen",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "qwen")
        self.assertEqual(request.provider, "qwen")
        self.assertEqual(request.metadata["model_preference"], "qwen3-coder-plus")

    def test_build_request_skips_default_model_for_primary_copilot_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "copilot": {
                    "id": "copilot",
                    "display_name": "Copilot",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "copilot",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "copilot")
        self.assertEqual(request.provider, "copilot")
        self.assertNotIn("model_preference", request.metadata)

    def test_build_request_keeps_agent_specific_model_for_copilot_alias(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "grok": {
                    "id": "grok",
                    "display_name": "Copilot (legacy alias)",
                    "provider": "copilot",
                    "adapter": "copilot_local",
                }
            },
            "providers": {
                "copilot": {
                    "delivery_mode": "copilot_local",
                    "model_preference": {
                        "default": None,
                        "grok": "grok-code-fast-1",
                    },
                }
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "grok",
                "message": "wake",
            },
        )

        self.assertEqual(request.agent_id, "grok")
        self.assertEqual(request.provider, "copilot")
        self.assertEqual(request.metadata["model_preference"], "grok-code-fast-1")
        self.assertEqual(request.metadata["logical_agent_id"], "grok")
        self.assertEqual(request.metadata["target_display_name"], "Copilot (legacy alias)")

    def test_build_request_can_target_codex_worker_slot_with_logical_identity(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-1",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                    "slot_id": "codex1-2",
                },
            },
            "providers": {
                "codex": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }

        request = supervisor.build_request(
            config,
            {
                "target_agent": "codex",
                "target_display_name": "Codex",
                "message": "wake",
                "task_id": "BFF-CONSOL-011",
                "context_files": [],
            },
            agent_id_override="codex1_2",
        )

        self.assertEqual(request.agent_id, "codex1_2")
        self.assertEqual(request.provider, "codex1-2")
        self.assertEqual(request.metadata["logical_agent_id"], "codex")
        self.assertEqual(request.metadata["dispatch_slot_id"], "codex1_2")
        self.assertEqual(request.metadata["dispatch_slot"], "codex1-2")
        self.assertEqual(request.metadata["target_display_name"], "Codex")

    def test_select_dispatch_agent_id_chooses_free_codex_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "display_name": "Codex",
                    "provider": "codex1-1",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "display_name": "Codex",
                    "provider": "codex1-2",
                    "adapter": "codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "providers": {
                "codex1-1": {"delivery_mode": "codex", "quota_group": "codex1"},
                "codex1-2": {"delivery_mode": "codex", "quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "status": "running",
                }
            }
        }

        selected = supervisor.select_dispatch_agent_id(config, state, "codex", {"running"})

        self.assertEqual(selected, "codex1_2")

    def test_skips_stale_owned_dispatch_event_after_task_completion(self) -> None:
        queued_task = {
            "id": "BUS-VAL-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T11:45:16Z",
        }
        queued_event = supervisor.build_dispatch_event(
            queued_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-001": queued_task},
        )
        queue_payload = {
            "event_id": "evt-stale",
            "event_key": queued_event["key"],
            "task_id": "BUS-VAL-001",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        current_status = {
            "tasks": [
                {
                    **queued_task,
                    "status": "done",
                    "last_update": "2026-04-05T12:00:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value=current_status),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("stale event should not start a worker")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-stale"]
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["skip_reason"], "stale_dispatch_event")
        self.assertIn("processed_at", record)
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_starts_current_owned_dispatch_event(self) -> None:
        current_task = {
            "id": "BUS-VAL-004",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-05T14:54:01Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Codex",
            "owned_in_progress_dispatch",
            {"BUS-VAL-004": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-004",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = object()
        delivery = {"manual_confirmation_required": False, "auto_delivered": True}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request) as build_request,
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-123", delivery)) as start_worker,
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "run-123")
        build_request.assert_called_once_with(self.config, queue_payload)
        start_worker.assert_called_once()
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)

    def test_failed_auto_lane_dispatch_does_not_create_manual_pending_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-failed-auto",
            "task_id": "BUS-VAL-005",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-005",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "CLI auth unavailable", None)),
            mock.patch.object(supervisor, "classify_worker_failure", return_value={"kind": "auth", "label": "authentication"}),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "CLI auth unavailable", "kind": "auth"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True) as mark_provider_dispatch_paused,
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-failed-auto"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "CLI auth unavailable")
        self.assertEqual(state["workers"], {})
        mark_provider_dispatch_paused.assert_called_once()

    def test_process_queue_skips_not_auto_ready_provider_without_starting_worker(self) -> None:
        current_task = {
            "id": "BUS-VAL-005B",
            "status": "review",
            "owner": "Codex",
            "reviewer": "Claude2",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Claude2",
            "review_ready_dispatch",
            {"BUS-VAL-005B": current_task},
        )
        queue_payload = {
            "event_id": "evt-not-ready",
            "event_key": current_event["key"],
            "task_id": "BUS-VAL-005B",
            "target_agent": "claude2",
            "target_display_name": "Claude2",
            "provider": "claude2",
            "reason": "review_ready_dispatch",
            "message": "wake",
            "context_files": [],
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {"claude2": {"auth_ready": False}},
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("not-ready provider should not start")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.process_queue(self.config, state, provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-not-ready"]
        self.assertEqual(record["status"], "failed")
        self.assertIn("Auto dispatch unavailable for claude2", record["error"])
        self.assertEqual(state["workers"], {})
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "wake_skipped")

    def test_process_queue_records_capacity_wait_metrics(self) -> None:
        current_task = {
            "id": "BUS-VAL-CAP",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-capacity-wait",
            "task_id": "BUS-VAL-CAP",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-CAP",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(
                supervisor,
                "agent_auto_dispatch_block_reason",
                return_value="quota group codex1 already has 1/1 active worker(s)",
            ),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("capacity wait should not start")),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-capacity-wait"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["capacity_wait_count"], 1)
        metrics = state["worker_runtime_metrics"]
        self.assertEqual(metrics["totals"]["capacity_pending_queue_events"], 1)
        self.assertEqual(
            metrics["last_measurements"]["dispatch_capacity_wait"]["details"]["queue_event_id"],
            "evt-capacity-wait",
        )

    def test_retryable_capacity_start_failure_schedules_queue_retry(self) -> None:
        current_task = {
            "id": "BUS-VAL-006",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-13T14:20:00Z",
        }
        queue_payload = {
            "event_id": "evt-retryable-capacity",
            "task_id": "BUS-VAL-006",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "provider": "codex",
            "reason": "owned_in_progress_dispatch",
            "message": "wake",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        request = supervisor.DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="BUS-VAL-006",
            reason="owned_in_progress_dispatch",
        )

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "build_request", return_value=request),
            mock.patch.object(supervisor, "start_worker_for_request", return_value=(False, "status: 429 RESOURCE_EXHAUSTED", None)),
            mock.patch.object(
                supervisor,
                "classify_worker_failure",
                return_value={"kind": "capacity_retryable", "label": "capacity/429", "transient": True},
            ),
            mock.patch.object(supervisor, "summarize_failure_reason", return_value={"summary": "Rate limited", "kind": "capacity_retryable"}),
            mock.patch.object(supervisor, "write_failure_evidence", return_value=None),
            mock.patch.object(supervisor, "record_task_failure_streak", return_value=1),
            mock.patch.object(supervisor, "mark_provider_dispatch_paused", return_value=True),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as maybe_reassign_task_after_worker_failure,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-retryable-capacity"]
        self.assertEqual(record["status"], "retry_backoff")
        self.assertEqual(record["error"], "Rate limited")
        self.assertEqual(record["retry_count"], 1)
        self.assertIsNotNone(record["next_retry_at"])
        maybe_reassign_task_after_worker_failure.assert_not_called()
        self.assertEqual(state["workers"], {})

    def test_dispatcher_can_requeue_same_task_after_previous_failure(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
            "last_update": "2026-04-06T09:00:00Z",
            "artifacts": ["services/registry/promotion/"],
            "next": "continue",
        }
        state = {
            "queue": {
                "events": {
                    "evt-old": {
                        "status": "failed",
                        "run_id": "old-run",
                    }
                }
            },
            "workers": {
                "old-run": {
                    "run_id": "old-run",
                    "queue_event_id": "evt-old",
                    "task_id": "REG-002",
                    "agent_id": "codex",
                    "status": "failed",
                }
            },
            "seen_event_keys": {"dispatcher:Codex:REG-002:owned_in_progress_dispatch:stale-signature": "2026-04-06T08:59:00Z"},
        }
        status = {"tasks": [current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_queues_multiple_codex_tasks_up_to_worker_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for index in range(1, 5):
            config["agents"][f"codex1_{index}"] = {
                "id": f"codex1_{index}",
                "display_name": "Codex",
                "provider": f"codex1-{index}",
                "adapter": "codex",
                "dispatch_slot_for": "codex",
            }
            config["providers"][f"codex1-{index}"] = {
                "delivery_mode": "codex",
                "quota_group": "codex1",
            }
        status = {
            "tasks": [
                {
                    "id": f"BFF-CONSOL-0{index}",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": f"2026-05-13T04:0{index}:00Z",
                }
                for index in range(1, 5)
            ]
        }
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_task_ids, ["BFF-CONSOL-01", "BFF-CONSOL-02", "BFF-CONSOL-03", "BFF-CONSOL-04"])
        self.assertTrue(all(call.args[1]["target_agent"] == "Codex" for call in queue_delivery_event.call_args_list))

    def test_weighted_dispatch_agent_ids_match_target_workload_ratio(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["ready_dispatcher"] = {
            "target_workload": {
                "Claude": 10,
                "Claude2": 5,
                "Gemini": 5,
                "Gemini2": 5,
                "Codex": 35,
                "Codex2": 35,
                "Copilot": 5,
            }
        }
        config["agents"] = {
            "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
            "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
            "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
            "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
        }

        sequence = supervisor.weighted_dispatch_agent_ids(config, supervisor.ready_dispatch_settings(config))
        counts = {agent_id: sequence.count(agent_id) for agent_id in config["agents"]}

        self.assertEqual(len(sequence), 20)
        self.assertEqual(
            counts,
            {
                "claude": 2,
                "claude2": 1,
                "gemini": 1,
                "gemini2": 1,
                "codex": 7,
                "codex2": 7,
                "copilot": 1,
            },
        )

    def test_persisted_weighted_cursor_prevents_review_starvation(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "enabled": True,
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
                "max_dispatches_per_tick": 1,
                "target_workload": {"Antigravity": 1, "Codex": 1},
                "helper_claim": {"enabled": False},
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                },
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "OWNER-FIRST",
                    "status": "todo",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                },
                {
                    "id": "REVIEW-LATER",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                },
            ]
        }
        state = runtime_state.default_state()
        first_round: list[dict[str, Any]] = []
        second_round: list[dict[str, Any]] = []

        common_patches = (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=(set(), set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(
                supervisor,
                "utc_now",
                return_value="2026-07-31T12:00:00Z",
            ),
        )
        with contextlib.ExitStack() as stack:
            for patcher in common_patches:
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "queue_delivery_event",
                    side_effect=lambda _config, event: first_round.append(event) or True,
                )
            )
            self.assertTrue(supervisor.dispatch_ready_tasks(config, state, provider_report={}))

        self.assertEqual(first_round[0]["task_id"], "OWNER-FIRST")
        self.assertEqual(state["ready_dispatcher"]["weighted_cursor"], 1)
        self.assertEqual(state["ready_dispatcher"]["weighted_cursor_revision"], 1)

        # Simulate the end-of-loop save/reload boundary that previously erased
        # the cursor and restarted every round from Antigravity.
        state = runtime_state.migrate_state(deepcopy(state))
        with contextlib.ExitStack() as stack:
            for patcher in (
                mock.patch.object(supervisor, "load_status", return_value=status),
                mock.patch.object(supervisor, "load_event_queue", return_value=[]),
                mock.patch.object(
                    supervisor,
                    "outstanding_delivery_indexes",
                    return_value=(set(), set(), set()),
                ),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
                mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
                mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
                mock.patch.object(
                    supervisor,
                    "utc_now",
                    return_value="2026-07-31T12:00:00Z",
                ),
                mock.patch.object(
                    supervisor,
                    "queue_delivery_event",
                    side_effect=lambda _config, event: second_round.append(event) or True,
                ),
            ):
                stack.enter_context(patcher)
            self.assertTrue(supervisor.dispatch_ready_tasks(config, state, provider_report={}))

        self.assertEqual(second_round[0]["task_id"], "REVIEW-LATER")
        self.assertEqual(second_round[0]["target_agent"], "Codex")
        self.assertEqual(second_round[0]["reason"], "review_ready_dispatch")
        self.assertEqual(state["ready_dispatcher"]["weighted_cursor"], 0)
        self.assertEqual(state["ready_dispatcher"]["weighted_cursor_revision"], 2)
        self.assertEqual(
            state["ready_dispatcher"]["weighted_cursor_updated_at"],
            "2026-07-31T12:00:00Z",
        )

    def test_dispatcher_fails_closed_on_owner_self_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "enabled": True,
                "review_statuses": ["review"],
                "active_worker_statuses": ["running"],
                "helper_claim": {"enabled": False},
            },
            "agents": {
                "codex": {
                    "id": "codex",
                    "display_name": "Codex",
                    "provider": "codex",
                }
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "INVALID-SELF-REVIEW",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Codex",
                    "depends_on": [],
                }
            ]
        }
        state = runtime_state.default_state()

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=(set(), set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "normalize_mainline_task_assignment", return_value=False),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                state,
                provider_report={},
                agent_ids_override=["codex"],
            )

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatcher_queues_owner_finalize_after_review_approved(self) -> None:
        current_task = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T15:00:00Z",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        dependency = {
            "id": "REG-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T14:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")),
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REG-002")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_dispatcher_waits_for_done_not_review_approved_dependencies(self) -> None:
        current_task = {
            "id": "FB-003",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": ["REG-002"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        dependency = {
            "id": "REG-002",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-001"],
            "last_update": "2026-04-06T14:00:00Z",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [dependency, current_task]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")),
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-003", queued_task_ids)

    def test_dispatcher_accepts_archived_done_dependency(self) -> None:
        current_task = {
            "id": "FB-004",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-100"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-100":
                    return "done"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return task_id == "REG-100"

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertIn("FB-004", queued_task_ids)

    def test_dispatcher_rejects_archived_superseded_dependency(self) -> None:
        current_task = {
            "id": "FB-005",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["REG-200"],
            "last_update": "2026-04-06T15:00:00Z",
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {"tasks": [current_task]}

        class FakeResolver:
            def __init__(self, task_lookup):
                self.task_lookup = task_lookup

            def dependency_status(self, task_id):
                if task_id == "REG-200":
                    return "superseded"
                task = self.task_lookup.get(task_id) or {}
                return str(task.get("status") or "missing")

            def dependency_satisfied(self, task_id):
                return False

        with (
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertNotIn("FB-005", queued_task_ids)

    def test_discussion_planning_materialization_treats_archived_task_as_already_materialized(self) -> None:
        planning_state = {
            "status": "accepted",
            "human_gate_status": "approved",
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "proposed_execution_tasks": [{"id": "LOOP-001"}],
        }

        class FakeResolver:
            def __init__(self, _task_lookup):
                pass

            def snapshot(self, task_id):
                if task_id == "LOOP-001":
                    return {"task_id": "LOOP-001"}
                return None

        with (
            mock.patch.object(supervisor, "load_json", return_value={"tasks": []}),
            mock.patch.object(supervisor, "config_path", return_value=Path("/tmp/ai-status.json")),
            mock.patch.object(supervisor, "TaskResolver", FakeResolver),
        ):
            needs_materialization = supervisor.discussion_planning_needs_materialization(self.config, planning_state)

        self.assertFalse(needs_materialization)

    def test_dispatcher_helper_claims_ready_todo_when_owner_is_busy_with_finalize(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_does_not_helper_claim_when_target_workload_would_exceed_cap(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "workload": {"Claude": 5, "Copilot": 95},
            "tasks": [
                {"id": "CL-001", "status": "blocked", "owner": "Claude", "reviewer": "Copilot", "depends_on": []},
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                *[
                    {
                        "id": f"CP-{index:03d}",
                        "status": "todo",
                        "owner": "Copilot",
                        "reviewer": "Claude",
                        "depends_on": [],
                    }
                    for index in range(17)
                ],
            ],
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        persist.assert_not_called()
        queue_delivery_event.assert_not_called()

    def test_dispatcher_does_not_helper_claim_when_owner_is_not_busy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Copilot")

    def test_dispatcher_helper_claims_ready_todo_when_idle_claim_enabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "FB-003",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                    "next": "Helper-claimed by idle Codex; designated reviewer Claude preserved.",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_spreads_paused_owner_work_to_registered_idle_agents(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "active_worker_statuses": ["running"],
                "max_tasks_per_agent_by_agent": {"Antigravity": 8},
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "claim_idle_work": True,
                    "include_registered_idle_agents": True,
                },
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Codex": ["Antigravity"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "antigravity2": {
                    "id": "antigravity2",
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                },
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-antigravity": {
                    "run_id": "run-antigravity",
                    "task_id": "BUSY-001",
                    "provider": "antigravity",
                    "agent_id": "antigravity",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Codex usage limit reached",
                    }
                }
            },
        }
        initial_status = {
            "tasks": [
                {
                    "id": "HELP-002",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Reviewer",
                    "depends_on": [],
                }
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "HELP-002",
                    "status": "in_progress",
                    "owner": "Antigravity2",
                    "reviewer": "Reviewer",
                    "depends_on": [],
                    "last_update": "2026-08-02T14:00:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Antigravity2")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Reviewer")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "HELP-002")
        self.assertEqual(queued_event["target_agent"], "Antigravity2")

    def test_dispatcher_does_not_queue_backlog_for_single_process_agent(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "active_worker_statuses": ["running"],
                "max_tasks_per_agent_by_agent": {"Copilot": 4},
                "helper_claim": {"enabled": False},
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-copilot": {
                    "run_id": "run-copilot",
                    "task_id": "BUSY-001",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {
                    "id": "NEXT-001",
                    "status": "todo",
                    "owner": "Copilot",
                    "reviewer": "Reviewer",
                    "depends_on": [],
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatcher_spreads_paused_review_to_registered_idle_reviewer(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "active_worker_statuses": ["running"],
                "helper_claim": {
                    "enabled": True,
                    "include_registered_idle_agents": True,
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "antigravity2": {
                    "id": "antigravity2",
                    "display_name": "Antigravity2",
                    "provider": "antigravity2",
                },
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Codex usage limit reached",
                    }
                }
            },
        }
        initial_status = {
            "tasks": [
                {
                    "id": "REVIEW-002",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                }
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "REVIEW-002",
                    "status": "review",
                    "owner": "Antigravity",
                    "reviewer": "Antigravity2",
                    "depends_on": [],
                    "last_update": "2026-08-02T14:05:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        self.assertEqual(persist.call_args.kwargs["new_owner"], "Antigravity")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Antigravity2")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "REVIEW-002")
        self.assertEqual(queued_event["target_agent"], "Antigravity2")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")

    def test_dispatcher_helper_claims_unrelated_task_during_failure_loop(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                    "disable_when_failure_loops": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:copilot": {
                        "task_id": "T-REVIEW",
                        "provider": "copilot",
                        "count": 3,
                    }
                }
            },
        }
        initial_status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Copilot", "depends_on": []},
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Copilot", "depends_on": []},
                {
                    "id": "FB-003",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        self.assertEqual(persist.call_args.kwargs["task_id"], "FB-003")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-003")
        self.assertEqual(queued_event["target_agent"], "Codex")

    def test_dispatcher_prefers_owned_work_before_idle_helper_claim(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {"id": "FOREIGN-001", "status": "todo", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {"id": "OWN-001", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_events = [call.args[1] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_events[0]["task_id"], "OWN-001")
        self.assertEqual(queued_events[0]["target_agent"], "Codex")

    def test_dispatcher_helper_claims_todo_when_owner_lane_is_disabled(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "disabled_agents": ["Gemini2"],
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                },
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Gemini2": ["Codex", "Claude"],
                }
            },
            "agents": {
                "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_helper_claims_sidecar_when_idle_claim_allows_sidecars(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                    "claim_sidecars_when_idle": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Gemini2": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "gemini2": {"id": "gemini2", "display_name": "Gemini2", "provider": "gemini2"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                    "last_update": "2026-05-13T09:31:00Z",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "FB-009-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex")

    def test_dispatcher_does_not_helper_claim_sidecar_when_owner_is_only_busy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex", "Claude", "Gemini"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "LP-005",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "LP-005", "status": "review_approved", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "FB-009-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Copilot",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "FB-009",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertFalse(changed)
        persist.assert_not_called()
        queue_delivery_event.assert_not_called()

    def test_dispatcher_helper_claims_in_progress_when_owner_lane_is_paused(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2999-01-01T00:00:00Z",
                        "summary": "Capacity / rate limit failure",
                    }
                }
            },
        }
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-006")
        self.assertEqual(kwargs["new_owner"], "Copilot")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Copilot")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_does_not_helper_claim_in_progress_when_owner_lane_is_healthy(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "paused_owner_task_statuses": ["in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Copilot", "Codex", "Claude"],
                }
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {"queue": {"events": {}}, "workers": {}}
        status = {
            "tasks": [
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_not_called()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_helper_claims_in_progress_when_owner_has_higher_priority_load(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo", "in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Claude"],
                }
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "WB-005",
                    "provider": "qwen",
                    "agent_id": "qwen",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {"id": "WB-006", "status": "in_progress", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-006")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Qwen")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-006")
        self.assertEqual(queued_event["target_agent"], "Claude")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_helper_claim_uses_persisted_authority_for_event_key(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo", "in_progress"],
                    "require_owner_higher_priority_load": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Claude"],
                }
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "WB-006",
                    "status": "in_progress",
                    "owner": "Qwen",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "last_update": "2026-05-09T09:00:00Z",
                },
            ]
        }
        persisted_status = {
            "tasks": [
                {"id": "WB-005", "status": "review_approved", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
                {
                    "id": "WB-006",
                    "status": "in_progress",
                    "owner": "Claude",
                    "reviewer": "Qwen",
                    "depends_on": [],
                    "last_update": "2026-05-09T10:00:00Z",
                    "next": "Helper-claimed by Claude while Qwen completes higher-priority work.",
                },
            ]
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-finalize": {
                    "run_id": "run-finalize",
                    "task_id": "WB-005",
                    "provider": "qwen",
                    "agent_id": "qwen",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertNotIn("2026-05-09T09:00:00Z", queued_event["key"])
        self.assertNotIn("2026-05-09T10:00:00Z", queued_event["key"])
        self.assertIn('"owner": "Claude"', queued_event["key"])
        self.assertIn('"reviewer": "Qwen"', queued_event["key"])
        self.assertEqual(queued_event["target_agent"], "Claude")
        self.assertEqual(queued_event["reason"], "owned_in_progress_dispatch")

    def test_dispatcher_helper_claim_preserves_designated_reviewer_when_target_is_not_reviewer(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Antigravity", "Codex"],
                }
            },
            "agents": {
                "antigravity": {"id": "antigravity", "display_name": "Antigravity", "provider": "antigravity"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "TASK-001", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "TASK-001",
                    "status": "todo",
                    "owner": "Antigravity",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                    "next": "Helper-claimed by idle Antigravity; designated reviewer Codex preserved.",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "TASK-001")
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertEqual(kwargs["new_reviewer"], "Codex")

    def test_dispatcher_helper_claim_replaces_reviewer_when_target_is_reviewer(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {
                    "enabled": True,
                    "task_statuses": ["todo"],
                    "claim_idle_work": True,
                }
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Copilot": ["Codex"],
                }
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "TASK-002", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
            ]
        }
        persisted_status = {
            "tasks": [
                {
                    "id": "TASK-002",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Copilot",
                    "depends_on": [],
                    "last_update": "2026-05-13T09:30:00Z",
                    "next": "Helper-claimed by idle Codex; previous owner Copilot becomes reviewer.",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, persisted_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}}, agent_ids_override=["codex"])

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "TASK-002")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Copilot")

    def test_dispatcher_reassigns_mainline_qwen_owner_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Qwen", "reviewer": "Claude", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-011", "status": "todo", "owner": "Codex", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-011")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-011")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_reassigns_mainline_qwen_reviewer_before_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                "owner_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
                "reviewer_fallbacks": {
                    "Qwen": ["Codex", "Claude", "Copilot"],
                },
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        initial_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Qwen", "depends_on": []},
            ]
        }
        normalized_status = {
            "tasks": [
                {"id": "WB-012", "status": "review", "owner": "Claude", "reviewer": "Codex", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, normalized_status]),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "WB-012")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-012")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "review_ready_dispatch")

    def test_dispatcher_still_allows_qwen_sidecar_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "agents": {
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "WB-013-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Qwen",
                    "reviewer": "Claude",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "WB-013",
                    "helper_kind": "review_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "WB-013-SIDECAR-REVIEW")
        self.assertEqual(queued_event["target_agent"], "Qwen")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_prefers_mainline_work_over_sidecar_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "BFF-FINAL-006",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Codex2",
                    "depends_on": [],
                },
                {
                    "id": "BFF-FINAL-010-SIDECAR-SMOKE",
                    "status": "review",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "BFF-FINAL-010",
                    "helper_kind": "smoke_matrix",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "BFF-FINAL-006")
        self.assertEqual(queued_event["target_agent"], "Codex")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_uses_spare_codex_slot_for_sidecar_after_primary_work(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2"]
        for index in range(1, 3):
            config["agents"][f"codex1_{index}"] = {
                "id": f"codex1_{index}",
                "display_name": "Codex",
                "provider": f"codex1-{index}",
                "adapter": "codex",
                "dispatch_slot_for": "codex",
            }
            config["providers"][f"codex1-{index}"] = {
                "delivery_mode": "codex",
                "quota_group": "codex1",
            }
        status = {
            "tasks": [
                {
                    "id": "SPRINT-8-CLOSEOUT",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "last_update": "2026-05-18T04:05:09Z",
                },
                {
                    "id": "OSS-FINRL-V2-001-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Gemini2",
                    "depends_on": [],
                    "last_update": "2026-05-18T02:55:51Z",
                    "task_class": "sidecar",
                    "helper_parent": "OSS-FINRL-V2-001",
                    "helper_kind": "review_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, {"queue": {"events": {}}, "workers": {}})

        self.assertTrue(changed)
        queued_task_ids = [call.args[1]["task_id"] for call in queue_delivery_event.call_args_list]
        self.assertEqual(queued_task_ids, ["SPRINT-8-CLOSEOUT", "OSS-FINRL-V2-001-SIDECAR-REVIEW"])

    def test_dispatcher_dispatches_existing_sidecar_when_parent_blocks_new_sidecars(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "agents": {
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {
                    "id": "BFF-FINAL-010-SIDECAR-BFF-HANDOFF",
                    "status": "todo",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "helper_parent": "BFF-FINAL-010",
                    "helper_kind": "bff_handoff_packet",
                },
            ]
        }
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {"sidecar_blocked_parents": ["BFF-FINAL-010"]},
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)

        self.assertTrue(changed)
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "BFF-FINAL-010-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Codex2")
        self.assertEqual(queued_event["reason"], "owned_ready_dispatch")

    def test_dispatcher_skips_agent_when_provider_report_says_not_auto_ready(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
            "providers": {},
        }
        status = {
            "tasks": [
                {"id": "AUTO-READY-001", "status": "review", "owner": "Codex", "reviewer": "Claude2", "depends_on": []},
            ]
        }
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude CLI is installed but not authenticated.",
                }
            },
            "providers": {
                "claude2": {
                    "local_cli_worker_supported": False,
                    "supports_auto_approve": False,
                    "auth_ready": False,
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                config,
                {"queue": {"events": {}}, "workers": {}},
                provider_report=provider_report,
            )

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_skips_duplicate_start_when_active_worker_already_exists(self) -> None:
        current_task = {
            "id": "P3-001",
            "status": "review",
            "owner": "Claude",
            "reviewer": "Gemini",
            "depends_on": [],
            "last_update": "2026-04-06T05:30:43Z",
        }
        current_event = supervisor.build_dispatch_event(
            current_task,
            "Gemini",
            "review_ready_dispatch",
            {"P3-001": current_task},
        )
        queue_payload = {
            "event_id": "evt-current",
            "event_key": current_event["key"],
            "task_id": "P3-001",
            "target_agent": "gemini",
            "target_display_name": "Gemini",
            "reason": "review_ready_dispatch",
            "message": "wake",
        }
        state = {
            "queue": {"events": {}},
            "workers": {
                "gemini-run-1": {
                    "run_id": "gemini-run-1",
                    "queue_event_id": "evt-current",
                    "status": "running",
                }
            },
        }

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[queue_payload]),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [current_task]}),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("duplicate queue event should not start another worker")),
            mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True) as sync_dispatched_task_status,
        ):
            changed = supervisor.process_queue(self.config, state, self.provider_report)

        self.assertTrue(changed)
        record = state["queue"]["events"]["evt-current"]
        self.assertEqual(record["status"], "started")
        self.assertEqual(record["run_id"], "gemini-run-1")
        sync_dispatched_task_status.assert_called_once_with(self.config, queue_payload)


class DispatchStatusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "ai_status.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.status_path = self.root / "ai-status.json"
        self.status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "APP-002-W1-FRONT-HANDOFF",
                            "status": "todo",
                            "owner": "Copilot",
                            "reviewer": "Codex",
                            "depends_on": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.status_path),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
        }

    def test_sync_dispatched_task_status_starts_owned_todo_task(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "copilot",
            "target_display_name": "Copilot",
            "reason": "owned_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run", return_value=mock.Mock(returncode=0, stderr="", stdout="")) as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertTrue(changed)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[2], "start")
        self.assertEqual(command[3], "APP-002-W1-FRONT-HANDOFF")
        self.assertIn("Supervisor auto-started", command[4])
        self.assertEqual(run_mock.call_args.kwargs["env"]["AI_NAME"], "Copilot")

    def test_sync_dispatched_task_status_skips_review_dispatch(self) -> None:
        event = {
            "task_id": "APP-002-W1-FRONT-HANDOFF",
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "review_ready_dispatch",
        }

        with mock.patch.object(supervisor.subprocess, "run") as run_mock:
            changed = supervisor.sync_dispatched_task_status(self.config, event)

        self.assertFalse(changed)
        run_mock.assert_not_called()


class RunOnceSupervisorStateTests(unittest.TestCase):
    def test_discussion_planning_needs_materialization_for_accepted_approved_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            class FakeResolver:
                def __init__(self, _task_lookup):
                    pass

                def snapshot(self, _task_id):
                    return None

            with mock.patch.object(supervisor, "TaskResolver", FakeResolver):
                self.assertTrue(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_current_session_tasks_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "LOOP-001",
                                "source_plane": "planning",
                                "source_ref": {"session_id": "phase3-session"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_discussion_planning_skips_materialization_when_session_already_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            config = {
                "paths": {"status_file": str(status_file)},
                "schema": {"tasks_path": "tasks", "task_id_field": "id"},
            }
            planning_state = {
                "status": "accepted",
                "human_gate_status": "approved",
                "materialized_at": "2026-04-19T03:40:25Z",
                "session_id": "phase7-session",
                "proposed_execution_tasks": [{"id": "OSS-004A"}],
            }

            self.assertFalse(supervisor.discussion_planning_needs_materialization(config, planning_state))

    def test_heartbeat_lag_seconds_reports_gap(self) -> None:
        lag = supervisor.heartbeat_lag_seconds(
            "2026-04-06T12:00:00Z",
            "2026-04-06T12:00:12Z",
        )

        self.assertEqual(lag, 12.0)

    def test_run_once_re_stamps_current_pid_after_watch_reload(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }
        saved_state: dict[str, object] = {}

        def capture_save(_config: dict[str, object], state: dict[str, object]) -> None:
            saved_state.clear()
            saved_state.update(state)

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None),
            mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False),
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False),
            mock.patch.object(supervisor, "dispatch_chair_review", return_value=False),
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "utc_now", return_value="2026-06-30T04:30:09Z"),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state", side_effect=capture_save),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        self.assertEqual(saved_state["supervisor"]["pid"], os.getpid())
        self.assertIsNotNone(saved_state["supervisor"]["last_heartbeat_at"])
        self.assertEqual(saved_state["supervisor"]["started_at"], saved_state["supervisor"]["last_heartbeat_at"])

    def test_run_once_prioritizes_discussion_planning_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with (
            mock.patch.object(supervisor, "write_supervisor_pid"),
            mock.patch.object(supervisor, "load_runtime_state", side_effect=[dict(initial_state), dict(initial_state)]),
            mock.patch.object(supervisor, "prune_stale_approvals", return_value=False),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "run_scan", return_value=False),
            mock.patch.object(supervisor, "sync_coordination_files", return_value=False),
            mock.patch.object(supervisor, "poll_workers", return_value=False),
            mock.patch.object(supervisor, "reconcile_queue_records", return_value=False),
            mock.patch.object(supervisor, "prune_event_queue", return_value=False),
            mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False),
            mock.patch.object(supervisor, "load_discussion_planning_state", return_value={"status": "active", "planning_mode": "discussion_planning", "readouts": {}}),
            mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True) as dispatch_discussion_planning,
            mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=False) as dispatch_ready_tasks,
            mock.patch.object(supervisor, "dispatch_chair_review", return_value=False) as dispatch_chair_review,
            mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False) as dispatch_underutilization_sidecars,
            mock.patch.object(supervisor, "process_queue", return_value=False),
            mock.patch.object(supervisor, "sync_github_bus", return_value=False),
            mock.patch.object(supervisor, "trim_worker_history"),
            mock.patch.object(supervisor, "trim_seen_events"),
            mock.patch.object(supervisor, "save_runtime_state"),
        ):
            supervisor.run_once(config, watch=True, replay=False)

        dispatch_discussion_planning.assert_called_once()
        dispatch_ready_tasks.assert_not_called()
        dispatch_chair_review.assert_not_called()
        dispatch_underutilization_sidecars.assert_not_called()

    def test_run_once_dispatches_ready_tasks_after_failure_loop_chair_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
            stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
            stack.enter_context(mock.patch.object(supervisor, "expire_provider_dispatch_pauses", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
            stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=None))
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "watchdog_safe_mode_active", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "chair_review_failure_loop_details", return_value=[{"task_id": "ASST-OCGW-004"}]))
            dispatch_chair_review = stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=True))
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            dispatch_underutilization_sidecars = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False)
            )
            stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "check_branch_drift", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "maybe_auto_commit_archive", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_chair_review.assert_called_once()
        dispatch_ready_tasks.assert_called_once()
        dispatch_underutilization_sidecars.assert_called_once()

    def test_run_once_watchdog_safe_mode_suppresses_new_dispatch(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {},
            "watcher": {},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {},
        }
        initial_state = {
            "queue": {"events": {}},
            "workers": {},
            "approvals": {},
            "watchdog": {
                "safe_mode_until": "2999-01-01T00:00:00Z",
                "safe_mode_reason": "stale_heartbeat",
            },
            "supervisor": {
                "pid": 61209,
                "started_at": "2026-04-05T12:44:57Z",
                "last_heartbeat_at": "2026-04-06T04:17:26Z",
            },
        }

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
            stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
            stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
            stack.enter_context(mock.patch.object(supervisor, "expire_provider_dispatch_pauses", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
            stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
            stack.enter_context(
                mock.patch.object(
                    supervisor,
                    "load_discussion_planning_state",
                    return_value={"status": "active", "planning_mode": "discussion_planning"},
                )
            )
            stack.enter_context(mock.patch.object(supervisor, "auto_materialize_discussion_planning", return_value=False))
            dispatch_discussion_planning = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=True)
            )
            dispatch_ready_tasks = stack.enter_context(mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True))
            dispatch_chair_review = stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=True))
            dispatch_underutilization_sidecars = stack.enter_context(
                mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=True)
            )
            process_queue = stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=True))
            stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "check_branch_drift", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
            stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
            stack.enter_context(mock.patch.object(supervisor, "prune_orphan_worktrees", return_value=False))
            stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
            stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
            stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
            write_activity_log = stack.enter_context(mock.patch.object(supervisor, "write_activity_log"))
            changed = supervisor.run_once(config, watch=False, replay=False)

        self.assertTrue(changed)
        dispatch_discussion_planning.assert_not_called()
        dispatch_ready_tasks.assert_not_called()
        dispatch_chair_review.assert_not_called()
        dispatch_underutilization_sidecars.assert_not_called()
        process_queue.assert_not_called()
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "watchdog_safe_mode_dispatch_suppressed")

    def test_run_supervisor_cycle_logs_and_continues_after_error(self) -> None:
        config = {"supervisor": {}}

        with (
            mock.patch.object(supervisor, "run_once", side_effect=RuntimeError("boom")) as run_once,
            mock.patch.object(supervisor, "console_log") as console_log,
        ):
            changed = supervisor.run_supervisor_cycle(config, watch=True, replay=True, quiet=True, verbose=False)

        self.assertFalse(changed)
        run_once.assert_called_once_with(
            config,
            watch=True,
            replay=True,
            quiet=True,
            verbose=False,
            once=False,
        )
        self.assertIn("RuntimeError: boom", console_log.call_args.args[0])
        self.assertTrue(console_log.call_args.kwargs["quiet"])

    def test_run_once_auto_materializes_accepted_session_before_execution_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_file = root / "ai-status.json"
            status_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
            script_dir = root / "scripts"
            script_dir.mkdir(parents=True, exist_ok=True)
            (script_dir / "planning_state.py").write_text("# test stub\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(status_file),
                    "activity_log": str(root / "activity-log.jsonl"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "supervisor": {},
                "watcher": {},
                "ready_dispatcher": {},
                "providers": {},
                "agents": {},
            }
            initial_state = {
                "queue": {"events": {}},
                "workers": {},
                "approvals": {},
                "supervisor": {
                    "pid": 61209,
                    "started_at": "2026-04-05T12:44:57Z",
                    "last_heartbeat_at": "2026-04-06T04:17:26Z",
                },
            }
            planning_state = {
                "status": "accepted",
                "planning_mode": "discussion_planning",
                "human_gate_status": "approved",
                "session_id": "phase3-session",
                "proposed_execution_tasks": [
                    {
                        "id": "LOOP-001",
                        "source_plane": "planning",
                        "source_ref": {"session_id": "phase3-session"},
                    }
                ],
            }

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(supervisor, "write_supervisor_pid"))
                stack.enter_context(mock.patch.object(supervisor, "load_runtime_state", return_value=dict(initial_state)))
                stack.enter_context(mock.patch.object(supervisor, "continue_or_skip_empty"))
                stack.enter_context(mock.patch.object(supervisor, "prune_stale_approvals", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_provider_report", return_value={}))
                stack.enter_context(mock.patch.object(supervisor, "sync_coordination_files", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "poll_workers", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "reconcile_queue_records", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "prune_event_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "refresh_chair_review_state", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "load_discussion_planning_state", return_value=planning_state))
                dispatch_discussion_planning = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_discussion_planning", return_value=False)
                )
                dispatch_ready_tasks = stack.enter_context(
                    mock.patch.object(supervisor, "dispatch_ready_tasks", return_value=True)
                )
                stack.enter_context(mock.patch.object(supervisor, "dispatch_chair_review", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "dispatch_underutilization_sidecars", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "process_queue", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "sync_github_bus", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "check_branch_drift", return_value=False))
                stack.enter_context(mock.patch.object(supervisor, "trim_worker_history"))
                stack.enter_context(mock.patch.object(supervisor, "trim_seen_events"))
                stack.enter_context(mock.patch.object(supervisor, "refresh_dashboard_runtime_artifacts"))
                stack.enter_context(mock.patch.object(supervisor, "log_runtime_summary"))
                stack.enter_context(mock.patch.object(supervisor, "save_runtime_state"))
                stack.enter_context(
                    mock.patch.object(
                        supervisor,
                        "TaskResolver",
                        type(
                            "FakeResolver",
                            (),
                            {
                                "__init__": lambda self, _task_lookup: None,
                                "snapshot": lambda self, _task_id: None,
                            },
                        ),
                    )
                )
                run_mock = stack.enter_context(
                    mock.patch.object(
                        supervisor.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess(
                            args=["python3", str(script_dir / "planning_state.py"), "materialize"],
                            returncode=0,
                            stdout="materialized",
                            stderr="",
                        ),
                    )
                )
                changed = supervisor.run_once(config, watch=False, replay=False)

            self.assertTrue(changed)
            dispatch_discussion_planning.assert_not_called()
            dispatch_ready_tasks.assert_called_once()
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0][-1], "materialize")


class SupervisorRuntimeFocusTests(unittest.TestCase):
    def test_discussion_planning_focus_overrides_execution_draining(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "event-queue.jsonl").write_text("", encoding="utf-8")
            config = {
                "paths": {
                    "event_queue": str(root / "event-queue.jsonl"),
                    "status_file": str(root / "ai-status.json"),
                },
                "schema": {
                    "tasks_path": "tasks",
                    "task_id_field": "id",
                    "assignee_field": "owner",
                    "reviewer_field": "reviewer",
                },
                "ready_dispatcher": {},
            }
            state = {
                "queue": {"events": {}},
                "workers": {
                    "exec-worker": {
                        "status": "manual_pending",
                        "reason": "owned_dispatch",
                    },
                    "planning-worker": {
                        "status": "started",
                        "reason": "discussion_planning_baton_dispatch",
                        "request_snapshot": {
                            "reason": "discussion_planning_baton_dispatch",
                            "metadata": {
                                "planning": {
                                    "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
                                    "mode": "discussion_planning",
                                }
                            },
                        },
                    },
                },
                "supervisor": {
                    "pid": 61209,
                    "focus_mode": "execution",
                    "mode_status": "active",
                },
            }
            planning_state = {
                "status": "active",
                "planning_mode": "discussion_planning",
                "session_id": "phase7-2026-04-18-ep4-ep5-execution-proof",
            }

            supervisor.stamp_supervisor_runtime_state(
                config,
                state,
                planning_state=planning_state,
                heartbeat_at="2026-04-18T14:40:00Z",
                lifecycle="running",
            )

            supervisor_state = state["supervisor"]
            self.assertEqual(supervisor_state["focus_mode"], "planning")
            self.assertEqual(supervisor_state["mode_status"], "active")
            self.assertIsNone(supervisor_state["mode_switch_requested"])
            self.assertEqual(supervisor_state["last_mode_switch_at"], "2026-04-18T14:40:00Z")
            self.assertEqual(supervisor_state["mode_occupancy"]["planning"]["running"], 1)
            self.assertEqual(supervisor_state["mode_occupancy"]["execution"]["pending"], 1)


class DiscussionPlanningDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
            },
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "copilot": {"id": "copilot", "display_name": "Copilot", "provider": "copilot"},
                "qwen": {"id": "qwen", "display_name": "Qwen", "provider": "qwen"},
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "gemini": {"delivery_mode": "gemini"},
                "codex": {"delivery_mode": "codex"},
                "copilot": {"delivery_mode": "copilot_local"},
                "qwen": {"delivery_mode": "qwen"},
            },
        }

    def test_dispatch_discussion_planning_queues_pending_readouts(self) -> None:
        planning_state = {
            "session_id": "phase1-2026-04-11",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Plan the Pantheon backend completion wave.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "readouts": {
                "Claude": {"status": "pending"},
                "Codex": {"status": "pending"},
                "Gemini": {"status": "pending"},
                "Qwen": {"status": "pending"},
                "Copilot": {"status": "pending"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 5)
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertEqual(codex_event["reason"], "discussion_planning_baton_dispatch")
        self.assertIn("starter-draft.md", "\n".join(codex_event["target_files"]))
        claude_event = next(row for row in rows if row["target_display_name"] == "Claude")
        self.assertIn("consensus-packet.md", "\n".join(claude_event["target_files"]))

    def test_dispatch_discussion_planning_uses_active_session_paths_and_owned_outputs(self) -> None:
        planning_dir = "docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop"
        planning_state = {
            "session_id": "phase3-2026-04-14-pantheon-console-loop",
            "planning_dir": planning_dir,
            "session_file": f"{planning_dir}/planning-session.json",
            "status": "active",
            "planning_mode": "discussion_planning",
            "summary": "Formalize the Pantheon Console closed loop.",
            "objective": "Define the canonical closed-loop coordination protocol and execution backlog for all 8 workbenches.",
            "baton_owner": "Codex",
            "next_reviewer": "Qwen",
            "current_round": 0,
            "consensus_status": "draft",
            "brief_files": [
                "Pantheon_總索引版系統分析文件.md",
                ".coordination/README.md",
            ],
            "artifacts": {
                "planning_readme": {"path": f"{planning_dir}/README.md"},
                "starter_draft": {"path": f"{planning_dir}/starter-draft.md"},
                "consensus_packet": {"path": f"{planning_dir}/consensus-packet.md"},
            },
            "expected_outputs": [
                {
                    "id": "coordination_loop_spec",
                    "path": f"{planning_dir}/coordination-loop-spec.md",
                    "owner": "Codex",
                }
            ],
            "readouts": {
                "Claude": {"status": "pending", "path": f"{planning_dir}/claude-readout.md"},
                "Codex": {"status": "pending", "path": f"{planning_dir}/codex-readout.md"},
                "Gemini": {"status": "pending", "path": f"{planning_dir}/gemini-readout.md"},
                "Qwen": {"status": "pending", "path": f"{planning_dir}/qwen-readout.md"},
                "Copilot": {"status": "pending", "path": f"{planning_dir}/copilot-readout.md"},
            },
        }
        state = {"queue": {"events": {}}, "workers": {}, "seen_event_keys": {}}

        with mock.patch.object(supervisor, "selected_shared_files", return_value=[self.root / "shared.md"]):
            changed = supervisor.dispatch_discussion_planning(self.config, state, planning_state)

        self.assertTrue(changed)
        rows = [
            json.loads(line)
            for line in (self.root / "event-queue.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        codex_event = next(row for row in rows if row["target_display_name"] == "Codex")
        self.assertIn(f"{planning_dir}/README.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/planning-session.json", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/codex-readout.md", codex_event["target_files"])
        self.assertIn(f"{planning_dir}/coordination-loop-spec.md", codex_event["target_files"])
        self.assertIn("本輪目標：Define the canonical closed-loop coordination protocol", codex_event["message"])

    def test_planning_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "phase1-2026-04-11-backend-completion",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "discussion_planning_baton_dispatch",
                "metadata": {
                    "planning": {
                        "session_id": "phase1-2026-04-11-backend-completion",
                        "mode": "discussion_planning",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_coordination_worker_matches_assignment_without_taskboard_entry(self) -> None:
        worker = {
            "task_id": "F-042",
            "agent_id": "codex",
            "request_snapshot": {
                "reason": "coordination:ui-done",
                "metadata": {
                    "coordination": {
                        "feature_id": "F-042",
                        "worker_kind": "front-sync-worker",
                        "payload_type": "ui-done",
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))
        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, {}))

    def test_detect_worker_failure_ignores_code_snippet_error_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "worker.log"
            log_path.write_text(
                "\n".join(
                    [
                        "class CommandStatusResponse(BaseModel):",
                        "    result: Optional[Dict[str, Any]] = None",
                        "    error: Optional[Dict[str, Any]] = None,",
                        "    audit: Optional[Dict[str, Any]] = None",
                        "class BffErrorEnvelope(BaseModel):",
                        "    error: BffErrorPayload",
                        "class ErrorResponse(BffErrorEnvelope):",
                        "    error: BFFError",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            worker = {"log_path": str(log_path)}
            self.assertIsNone(supervisor.detect_worker_failure(worker))

    def test_sidecar_review_does_not_preempt_mainline_worker(self) -> None:
        worker = {
            "task_id": "BFF-FINAL-006",
            "agent_id": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        task_map = {
            "BFF-FINAL-006": {
                "id": "BFF-FINAL-006",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "BFF-FINAL-010-SIDECAR-SMOKE": {
                "id": "BFF-FINAL-010-SIDECAR-SMOKE",
                "status": "review",
                "owner": "Codex2",
                "reviewer": "Codex",
                "depends_on": [],
                "task_class": "sidecar",
                "helper_parent": "BFF-FINAL-010",
                "helper_kind": "smoke_matrix",
            },
        }

        self.assertFalse(supervisor.higher_priority_ready_task_exists(self.config, worker, task_map))

    def test_priority_preemption_respects_logical_agent_slot_capacity(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-high": {
                    "run_id": "run-high",
                    "task_id": "BFF-CONSOL-016",
                    "agent_id": "codex1_1",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                },
                "run-low": {
                    "run_id": "run-low",
                    "task_id": "BFF-CONSOL-017",
                    "agent_id": "codex1_2",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                },
            },
        }
        task_map = {
            "BFF-CONSOL-016": {
                "id": "BFF-CONSOL-016",
                "status": "in_progress",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
            "BFF-CONSOL-017": {
                "id": "BFF-CONSOL-017",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Codex2",
                "depends_on": [],
            },
        }

        self.assertFalse(
            supervisor.higher_priority_ready_task_exists(
                config,
                state["workers"]["run-low"],
                task_map,
                state,
            )
        )

    def test_slotted_worker_is_not_preempted_for_non_urgent_owned_backlog(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["agents"]["codex"]["worker_slots"] = ["codex1_1", "codex1_2", "codex1_3", "codex1_4"]
        for slot_id in config["agents"]["codex"]["worker_slots"]:
            config["agents"][slot_id] = {
                "id": slot_id,
                "display_name": "Codex",
                "dispatch_slot_for": "codex",
                "provider": slot_id.replace("_", "-"),
            }
        state = {
            "queue": {"events": {}},
            "workers": {
                f"run-low-{index}": {
                    "run_id": f"run-low-{index}",
                    "task_id": f"BFF-CONSOL-0{20 + index}",
                    "agent_id": f"codex1_{index}",
                    "logical_agent_id": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
                for index in range(1, 5)
            },
        }
        task_map = {
            f"BFF-CONSOL-0{20 + index}": {
                "id": f"BFF-CONSOL-0{20 + index}",
                "status": "todo",
                "owner": "Codex",
                "reviewer": "Claude",
                "depends_on": [],
            }
            for index in range(1, 5)
        }
        task_map["BFF-CONSOL-099"] = {
            "id": "BFF-CONSOL-099",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }

        with mock.patch.object(supervisor, "load_event_queue", return_value=[]):
            self.assertFalse(
                supervisor.higher_priority_ready_task_exists(
                    config,
                    state["workers"]["run-low-1"],
                    task_map,
                    state,
                )
            )

    def test_dead_coordination_worker_is_completed_without_taskboard_entry(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "F-042",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {
                        "reason": "coordination:ui-done",
                        "metadata": {
                            "coordination": {
                                "feature_id": "F-042",
                                "worker_kind": "front-sync-worker",
                                "payload_type": "ui-done",
                            }
                        },
                    },
                }
            },
        }
        status = {"tasks": []}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")


class OrphanedQueueEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                    "retry_backoff",
                    "stalled",
                ],
                "orphaned_queue_event_grace_seconds": 300,
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }

    def _write_event(self, payload: dict[str, object]) -> None:
        (self.root / "event-queue.jsonl").write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_outstanding_delivery_indexes_ignore_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        agents, task_agents, event_keys = supervisor.outstanding_delivery_indexes(self.config, state)

        self.assertEqual(agents, set())
        self.assertEqual(task_agents, set())
        self.assertEqual(event_keys, set())

    def test_process_queue_skips_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "start_worker_for_request") as start_worker:
            changed = supervisor.process_queue(self.config, state, provider_report={})

        self.assertFalse(changed)
        start_worker.assert_not_called()
        self.assertEqual(state["queue"]["events"], {})

    def test_prune_event_queue_drops_stale_orphan_event(self) -> None:
        self._write_event(
            {
                "event_id": "coord-old",
                "created_at": "2000-01-01T00:00:00Z",
                "event_key": "coordination:front-sync-worker:RW-05-artifact-compare:ui-done:old",
                "task_id": "RW-05-artifact-compare",
                "target_agent": "codex",
                "target_display_name": "Codex",
                "provider": "codex",
                "reason": "coordination:ui-done",
                "message": "stale event",
            }
        )
        state = {"queue": {"events": {}}, "workers": {}}

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.prune_event_queue(self.config, state)

        self.assertTrue(changed)
        self.assertEqual((self.root / "event-queue.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(state["queue"]["events"], {})
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "queue_event_pruned")


class UnderutilizationSidecarDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "sidecar_catalog.json").write_text('{"templates": []}\n', encoding="utf-8")
        (self.root / "activity-log.jsonl").write_text("", encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "sidecar_catalog": str(self.root / "sidecar_catalog.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            },
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
                "dependency_done_statuses": ["done"],
            },
            "underutilization_dispatch": {
                "enabled": True,
                "require_recent_chair_signal": False,
                "threshold_ratio": 0.5,
                "continuous_window_seconds": 900,
                "cooldown_seconds": 900,
                "max_active_sidecars_per_agent": 1,
                "productive_worker_statuses": ["running", "waiting_approval", "suspended_approval", "retry_backoff"],
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini", "provider": "gemini"},
            },
        }

    def test_waits_full_window_before_creating_sidecars(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "underutilization": {}}

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("should not create before the window")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNotNone(state["underutilization"]["below_threshold_since"])
        self.assertIsNone(state["underutilization"].get("last_sidecar_wave_at"))
        write_activity_log.assert_not_called()

    def test_creates_visible_sidecar_after_continuous_low_utilization_window(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "TEL-001",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                    "request_snapshot": {"reason": "owned_in_progress_dispatch"},
                }
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        created_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Gemini",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "平行支援 APP-001，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:16:05Z",
        }
        status_before = {"tasks": [parent_task]}
        status_after = {"tasks": [parent_task, created_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[status_before, status_after]),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        create_sidecar_task.assert_called_once()
        kwargs = create_sidecar_task.call_args.kwargs
        self.assertEqual(kwargs["sidecar_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(kwargs["owner"], "Gemini")
        self.assertEqual(kwargs["reviewer"], "Claude")
        self.assertEqual(kwargs["helper_parent"], "APP-001")
        self.assertEqual(kwargs["helper_kind"], "bff_handoff_packet")
        self.assertFalse(kwargs["mutates_canonical"])
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "APP-001-SIDECAR-BFF-HANDOFF")
        self.assertEqual(queued_event["target_agent"], "Gemini")
        self.assertEqual(queued_event["task"]["task_class"], "sidecar")
        self.assertEqual(state["underutilization"]["last_sidecar_wave_at"], "2026-04-10T00:16:05Z")
        self.assertIn("created 1 visible sidecar", state["underutilization"]["last_sidecar_wave_reason"])
        self.assertIn("APP-001-SIDECAR-BFF-HANDOFF", state.get("tasks", {}))
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("sidecar_task_created", activity_types)
        self.assertIn("sidecar_wave_started", activity_types)

    def test_creates_all_assignable_sidecars_when_wave_limit_is_unset(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
            },
        }
        candidates = [
            {
                "sidecar_id": f"APP-00{index}-SIDECAR-REVIEW",
                "parent_task_id": f"APP-00{index}",
                "kind": "review_packet",
                "phase": "Phase 5",
                "title": f"Prepare APP-00{index} review packet",
                "summary_zh": "支援 review packet。",
                "reviewer": "Reviewer",
                "depends_on": [],
                "artifacts": [f"support/sidecars/APP-00{index}/packet.md"],
                "mutates_canonical": False,
                "priority": index,
            }
            for index in range(1, 4)
        ]
        status_before = {"tasks": []}
        status_after = [
            {
                "tasks": [
                    {
                        "id": candidate["sidecar_id"],
                        "status": "todo",
                        "owner": "Codex",
                        "reviewer": "Reviewer",
                        "task_class": "sidecar",
                        "helper_parent": candidate["parent_task_id"],
                        "helper_kind": candidate["kind"],
                    }
                ]
            }
            for candidate in candidates
        ]

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[status_before, *status_after]),
            mock.patch.object(supervisor, "eligible_idle_agents_for_sidecars", return_value=["Codex", "Gemini", "Claude"]),
            mock.patch.object(supervisor, "build_catalog_sidecar_candidates", return_value=candidates),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(create_sidecar_task.call_count, 3)
        self.assertEqual(queue_delivery_event.call_count, 3)
        self.assertIn("created 3 visible sidecar", state["underutilization"]["last_sidecar_wave_reason"])

    def test_explicit_sidecar_wave_limit_still_caps_for_incident_response(self) -> None:
        self.config["underutilization_dispatch"]["max_new_sidecars_per_wave"] = 1
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {"sidecar_approved_until": "2026-04-10T01:00:00Z"},
        }
        candidates = [
            {
                "sidecar_id": f"APP-00{index}-SIDECAR-REVIEW",
                "parent_task_id": f"APP-00{index}",
                "kind": "review_packet",
                "phase": "Phase 5",
                "title": f"Prepare APP-00{index} review packet",
                "summary_zh": "支援 review packet。",
                "reviewer": "Reviewer",
                "depends_on": [],
                "artifacts": [f"support/sidecars/APP-00{index}/packet.md"],
                "mutates_canonical": False,
                "priority": index,
            }
            for index in range(1, 3)
        ]
        status_after = {
            "tasks": [
                {
                    "id": "APP-001-SIDECAR-REVIEW",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Reviewer",
                    "task_class": "sidecar",
                    "helper_parent": "APP-001",
                    "helper_kind": "review_packet",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", side_effect=[{"tasks": []}, status_after]),
            mock.patch.object(supervisor, "eligible_idle_agents_for_sidecars", return_value=["Codex", "Gemini"]),
            mock.patch.object(supervisor, "build_catalog_sidecar_candidates", return_value=candidates),
            mock.patch.object(supervisor, "create_sidecar_task", return_value=(True, "")) as create_sidecar_task,
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(create_sidecar_task.call_count, 1)

    def test_chair_blocked_parent_prevents_new_sidecar_generation_only(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
            "chair_rotation": {
                "sidecar_approved_until": "2026-04-10T01:00:00Z",
                "sidecar_approval_max_sidecars": 1,
                "sidecar_blocked_parents": ["APP-001"],
            },
        }
        status = {
            "tasks": [
                {
                    "id": "APP-001",
                    "phase": "Phase 5: Persona and Application Surfaces",
                    "status": "todo",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "title": "Define BFF query surfaces",
                    "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
                    "artifacts": ["services/control-plane/bff/"],
                    "last_update": "2026-04-10T00:05:00Z",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("blocked parent should not create a new sidecar")),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["underutilization"]["last_sidecar_wave_at"], "2026-04-10T00:16:05Z")
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "underutilized but no sidecar candidates matched the catalog or dynamic fallback",
        )

    def test_resets_underutilization_timer_when_utilization_recovers(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {"run_id": "run-1", "task_id": "REG-004", "agent_id": "codex", "provider": "codex", "status": "running"},
                "run-2": {"run_id": "run-2", "task_id": "OSS-001", "agent_id": "gemini", "provider": "gemini", "status": "running"},
            },
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }

        changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertIsNone(state["underutilization"]["below_threshold_since"])

    def test_cooldown_prevents_duplicate_sidecar_wave(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": "2026-04-10T00:10:00Z",
                "last_sidecar_wave_reason": "already created a wave recently",
            },
        }

        with (
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("cooldown should prevent new sidecars")),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:20:00Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(state["underutilization"]["last_sidecar_wave_reason"], "already created a wave recently")

    def test_skips_duplicate_signature_when_matching_sidecar_already_exists(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {
                "below_threshold_since": "2026-04-10T00:00:00Z",
                "last_sidecar_wave_at": None,
                "last_sidecar_wave_reason": None,
            },
        }
        parent_task = {
            "id": "APP-001",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "todo",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
            "title": "Define BFF query surfaces",
            "summary_zh": "整理 operator console 與 workbench 的 BFF query contract。",
            "artifacts": ["services/control-plane/bff/"],
            "last_update": "2026-04-10T00:05:00Z",
        }
        existing_sidecar = {
            "id": "APP-001-SIDECAR-BFF-HANDOFF",
            "phase": "Phase 5: Persona and Application Surfaces",
            "status": "done",
            "owner": "Gemini",
            "reviewer": "Claude",
            "depends_on": [],
            "title": "Prepare APP-001 BFF and frontend handoff packet",
            "summary_zh": "已完成支援包。",
            "artifacts": ["support/sidecars/APP-001/APP-001-SIDECAR-BFF-HANDOFF.md"],
            "task_class": "sidecar",
            "auto_generated": True,
            "helper_parent": "APP-001",
            "helper_kind": "bff_handoff_packet",
            "mutates_canonical": False,
            "auto_created_by": "supervisor-underutilization",
            "last_update": "2026-04-10T00:07:00Z",
        }
        status = {"tasks": [parent_task, existing_sidecar]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_sidecar_catalog", return_value=[]),
            mock.patch.object(supervisor, "create_sidecar_task", side_effect=AssertionError("duplicate signature should not create another sidecar")),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-10T00:16:05Z"),
        ):
            changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "underutilized but no sidecar candidates matched the catalog or dynamic fallback",
        )
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertEqual(activity_types, ["sidecar_wave_skipped"])

    def test_requires_recent_chair_signal_when_gate_enabled(self) -> None:
        self.config["underutilization_dispatch"]["require_recent_chair_signal"] = True
        state = {
            "queue": {"events": {}},
            "workers": {},
            "underutilization": {},
            "chair_rotation": {"sidecar_approved_until": None},
        }

        changed = supervisor.dispatch_underutilization_sidecars(self.config, state)

        self.assertFalse(changed)
        self.assertEqual(
            state["underutilization"]["last_sidecar_wave_reason"],
            "awaiting chair review approval before creating sidecars",
        )


class ChairReviewDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        (self.root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "state_file": str(self.root / "state.json"),
                "activity_log": str(self.root / "activity-log.jsonl"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "providers": {},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "started",
                    "waiting_approval",
                    "manual_pending",
                    "retry_backoff",
                    "suspended_approval",
                    "stalled",
                    "fallback",
                ],
                "dependency_done_statuses": ["done"],
            },
            "chair_review": {
                "enabled": True,
                "cooldown_seconds": 1800,
                "candidates": ["Codex", "Codex2", "Claude", "Claude2"],
                "output_dir": str(self.root / "chair-reviews"),
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"},
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
                "claude2": {"id": "claude2", "display_name": "Claude2", "provider": "claude2"},
            },
        }

    def test_dispatch_chair_review_rotates_and_records_pending_report(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        self.assertEqual(state["chair_rotation"]["current_index"], 1)
        self.assertEqual(state["chair_rotation"]["pending_review_agent"], "Codex")
        self.assertTrue(str(state["chair_rotation"]["pending_review_path"]).endswith("-codex.md"))
        self.assertTrue(str(state["chair_rotation"]["pending_decision_path"]).endswith("-codex.json"))
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(len(events), 1)
        self.assertTrue(any(path.endswith("-codex.md") for path in events[0]["target_files"]))
        self.assertTrue(any(path.endswith("-codex.json") for path in events[0]["target_files"]))
        self.assertIn("Required Decision JSON Output", events[0]["message"])
        self.assertEqual(events[0]["metadata"]["workspace_task_id"], "chair-review-20260428-120000-codex")

    def test_dispatch_chair_review_skips_when_planning_active(self) -> None:
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        changed = supervisor.dispatch_chair_review(
            self.config,
            state,
            planning_state={"status": "active", "planning_mode": "discussion_planning", "readouts": {}},
        )

        self.assertFalse(changed)

    def test_dispatch_chair_review_respects_global_worker_cap(self) -> None:
        self.config["ready_dispatcher"]["max_concurrent_workers"] = 2
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}

        with (
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor,
                "outstanding_delivery_indexes",
                return_value=({"codex", "codex2"}, set(), set()),
            ),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertFalse(changed)
        self.assertEqual(supervisor.load_event_queue(self.config), [])

    def test_dispatch_chair_review_falls_through_busy_candidate(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "status": "running",
                }
            },
            "chair_rotation": {"current_index": 0},
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex2")
        self.assertEqual(state["chair_rotation"]["current_index"], 2)

    def test_dispatch_chair_review_falls_through_not_auto_ready_candidate(self) -> None:
        self.config["chair_review"]["candidates"] = ["Claude2", "Codex"]
        state = {"queue": {"events": {}}, "workers": {}, "chair_rotation": {"current_index": 0}}
        provider_report = {
            "agent_adapters": {
                "claude2": {
                    "supported": True,
                    "can_auto_deliver": False,
                    "notes": "Claude2 profile is not authenticated.",
                },
                "codex": {"supported": True, "can_auto_deliver": True},
            },
            "providers": {
                "claude2": {
                    "local_cli_worker_supported": False,
                    "supports_auto_approve": False,
                    "auth_ready": False,
                },
                "codex": {
                    "local_cli_worker_supported": True,
                    "supports_auto_approve": True,
                },
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:00:00Z"),
            mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
        ):
            changed = supervisor.dispatch_chair_review(
                self.config,
                state,
                planning_state=None,
                provider_report=provider_report,
            )

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        self.assertEqual(state["chair_rotation"]["current_index"], 0)

    def test_dispatch_chair_review_bypasses_cooldown_for_pending_approval(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "SVC-GOVERNANCE-API",
                            "worker_run_id": "run-1",
                            "tool_name": "Bash",
                            "risk_class": "needs_review",
                            "created_at": "2026-04-28T12:00:10Z",
                            "tool_input_preview": "docker compose config --quiet",
                        }
                    ],
                    "history": [],
                },
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:approval_triage")
        self.assertIn("approval_id=apr-1", events[0]["message"])

    def test_dispatch_chair_review_uses_idle_candidate_with_primary_work_for_pending_approval(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {
                "run-codex2": {
                    "run_id": "run-codex2",
                    "agent_id": "codex2",
                    "provider": "codex2",
                    "status": "running",
                }
            },
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
        }
        status = {
            "tasks": [
                {
                    "id": "PRIMARY-CODEX",
                    "status": "todo",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "BFF-LUV-FE-002",
                            "worker_run_id": "run-1",
                            "tool_name": "Agent",
                            "risk_class": "unknown",
                            "created_at": "2026-04-28T12:00:10Z",
                            "tool_input_preview": "Explore execute-plans repo BFF structure",
                        }
                    ],
                    "history": [],
                },
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Codex")
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:approval_triage")

    def test_dispatch_chair_review_bypasses_cooldown_for_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 0,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        events = supervisor.load_event_queue(self.config)
        self.assertEqual(events[0]["reason"], "chair_review:reassignment_triage")
        self.assertIn("Repeated Failure Details:", events[0]["message"])
        self.assertIn("task=T-REVIEW", events[0]["message"])
        self.assertIn('"reassignment_actions"', events[0]["message"])

    def test_dispatch_chair_review_skips_agent_in_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "chair_rotation": {
                "current_index": 1,
                "last_chair_run_at": "2026-04-28T12:00:00Z",
            },
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:05:00Z"),
        ):
            changed = supervisor.dispatch_chair_review(self.config, state, planning_state=None)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_chair_agent"], "Claude")

    def test_dispatch_ready_skips_task_waiting_for_chair_reassignment_triage(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertFalse(changed)
        queue_delivery_event.assert_not_called()

    def test_dispatch_ready_ignores_stale_logic_streak_after_environmental_failure(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Codex usage limit reached",
                        "last_failure_kind": "quota_terminal",
                        "last_environmental_failure_at": "2026-08-02T13:22:47Z",
                    }
                }
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event", return_value=True) as queue_delivery_event,
        ):
            changed = supervisor.dispatch_ready_tasks(
                self.config,
                state,
                agent_ids_override=["codex2"],
            )

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()

    def test_dispatch_ready_skips_only_task_agent_pair_in_failure_loop(self) -> None:
        state = {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                        "last_reason": "Worker exited before terminal state.",
                    }
                }
            },
        }
        status = {
            "tasks": [
                {"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"},
                {
                    "id": "T-FINALIZE",
                    "status": "review_approved",
                    "owner": "Codex2",
                    "reviewer": "Codex",
                    "approved_head": "1111111122222222333333334444444455555555",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")),
        ):
            changed = supervisor.dispatch_ready_tasks(self.config, state)

        self.assertTrue(changed)
        queue_delivery_event.assert_called_once()
        queued_event = queue_delivery_event.call_args.args[1]
        self.assertEqual(queued_event["task_id"], "T-FINALIZE")
        self.assertEqual(queued_event["target_agent"], "Codex2")
        self.assertEqual(queued_event["reason"], "owned_finalize_dispatch")

    def test_chair_worker_matches_current_assignment_without_task(self) -> None:
        worker = {
            "run_id": "chair-1",
            "agent_id": "codex",
            "provider": "codex",
            "task_id": None,
            "status": "running",
            "request_snapshot": {
                "reason": "chair_review:operational_review",
                "metadata": {
                    "chair": {
                        "mode": "chair_review",
                        "review_path": str(self.root / "chair-reviews" / "review.md"),
                    }
                },
            },
        }

        self.assertTrue(supervisor.worker_matches_current_assignment(self.config, worker, {}))

    def test_refresh_chair_review_approves_sidecars_from_decision_json(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nApprove a small sidecar wave.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "max_sidecars": 2,
                    "reason": "Idle workers are available and runnable support work exists.",
                    "blocked_by": [],
                    "blocked_sidecar_parents": ["SVC-RUNTIME-CONTROL-CLOSEOUT"],
                    "recommended_focus": ["SVC-EVIDENCE"],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["pending_decision_path"])
        self.assertEqual(rotation["sidecar_approved_until"], "2026-04-28T13:00:00Z")
        self.assertEqual(rotation["sidecar_approval_max_sidecars"], 2)
        self.assertTrue(rotation["last_review_sidecar_approved"])
        self.assertEqual(rotation["last_review_decision"], "approve_sidecars")
        self.assertEqual(rotation["sidecar_blocked_parents"], ["SVC-RUNTIME-CONTROL-CLOSEOUT"])
        self.assertEqual(rotation["last_review_recommended_focus"], ["SVC-EVIDENCE"])
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_approved_sidecars")

    def test_refresh_chair_review_syncs_completed_worktree_artifacts(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        workspace_path = self.root / "workers" / "pantheon" / "chair-review-20260428-codex"
        workspace_review_path = workspace_path / "chair-reviews" / "20260428-codex.md"
        workspace_decision_path = workspace_review_path.with_suffix(".json")
        workspace_review_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_review_path.write_text("# Summary\n\nApprove sidecars from worktree.\n", encoding="utf-8")
        workspace_decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Chair artifacts were produced in the isolated worker workspace.",
                    "blocked_by": [],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-chair": {"status": "completed"}}},
            "workers": {
                "chair-run": {
                    "status": "completed",
                    "workspace_path": str(workspace_path),
                    "request_snapshot": {
                        "reason": "chair_review:operational_review",
                        "metadata": {"chair": {"review_path": str(review_path)}},
                    },
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-chair",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(review_path.read_text(encoding="utf-8"), workspace_review_path.read_text(encoding="utf-8"))
        self.assertEqual(decision_path.read_text(encoding="utf-8"), workspace_decision_path.read_text(encoding="utf-8"))
        self.assertTrue(state["chair_rotation"]["last_review_valid"])
        event_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("chair_review_artifact_synced_from_worktree", event_types)
        self.assertIn("chair_review_approved_sidecars", event_types)

    def test_refresh_chair_review_syncs_worktree_artifacts_before_state_reconciles(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex2.md"
        decision_path = review_path.with_suffix(".json")
        workspace_path = self.root / "workers" / "pantheon" / "chair-review-20260428-codex2"
        workspace_review_path = workspace_path / "chair-reviews" / "20260428-codex2.md"
        workspace_decision_path = workspace_review_path.with_suffix(".json")
        workspace_review_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_review_path.write_text("# Summary\n\nApprove sidecars before reconcile.\n", encoding="utf-8")
        workspace_decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Runner finished before worker state reconciled.",
                    "blocked_by": [],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-chair": {"status": "started"}}},
            "workers": {
                "chair-run": {
                    "status": "running",
                    "workspace_path": str(workspace_path),
                    "request_snapshot": {
                        "reason": "chair_review:reassignment_triage",
                        "metadata": {"chair": {"review_path": str(review_path)}},
                    },
                    "runner_status": "completed",
                    "exit_code": 0,
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-chair",
                "pending_review_agent": "Codex2",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:20:00Z"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertTrue(review_path.exists())
        self.assertTrue(decision_path.exists())
        self.assertTrue(state["chair_rotation"]["last_review_valid"])
        event_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("chair_review_artifact_synced_from_worktree", event_types)
        self.assertIn("chair_review_approved_sidecars", event_types)

    def test_refresh_chair_review_denies_sidecars_and_clears_approval(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nHold sidecars.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "deny_sidecars",
                    "sidecar_approved": False,
                    "reason": "Human approval queue is blocking execution.",
                    "blocked_by": ["pending human approval"],
                    "recommended_focus": [],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
                "sidecar_approved_until": "2026-04-28T13:00:00Z",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["sidecar_approved_until"])
        self.assertFalse(rotation["last_review_sidecar_approved"])
        self.assertEqual(rotation["last_review_blocked_by"], ["pending human approval"])
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_denied_sidecars")

    def test_refresh_chair_review_applies_approval_actions(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nApprove compose validation.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed.",
                    "blocked_by": [],
                    "recommended_focus": [],
                    "approval_actions": [
                        {
                            "approval_id": "apr-1",
                            "decision": "allow",
                            "reason": "Low-risk compose config validation.",
                            "remember": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={"pending": [{"approval_id": "apr-1"}], "history": []},
            ),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_approval_actions"][0]["approval_id"], "apr-1")
        resolve_approval.assert_called_once_with(
            self.config,
            "apr-1",
            decision="allow",
            note=f"Chair review {supervisor.relpath(review_path)}: Low-risk compose config validation.",
            remember=False,
        )

    def test_refresh_chair_review_applies_reassignment_actions(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nMove the stuck review lane.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed after moving the stuck reviewer.",
                    "blocked_by": [],
                    "recommended_focus": [],
                    "reassignment_actions": [
                        {
                            "task_id": "T-REVIEW",
                            "role": "reviewer",
                            "from": "Codex2",
                            "to": "Claude",
                            "reason": "Codex2 repeatedly exits without approve/reject.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-REVIEW:codex2": {
                        "task_id": "T-REVIEW",
                        "provider": "codex2",
                        "count": 3,
                    }
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }
        status = {"tasks": [{"id": "T-REVIEW", "status": "review", "owner": "Codex", "reviewer": "Codex2"}]}

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist_task_reassignment,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_reassignment_actions"][0]["to"], "Claude")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})
        persist_task_reassignment.assert_called_once_with(
            self.config,
            task_id="T-REVIEW",
            new_owner="Codex",
            new_reviewer="Claude",
            message="Chair reassigned review from Codex2 to Claude: Codex2 repeatedly exits without approve/reject.",
            handoff_to="Claude",
            handoff_from="Codex2",
        )

    def test_refresh_chair_review_applies_blocked_owner_rescue_action(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nRescue blocked owner lane.\n", encoding="utf-8")
        decision_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "decision": "approve_sidecars",
                    "sidecar_approved": True,
                    "approval_ttl_minutes": 45,
                    "reason": "Execution can proceed after moving the auth-blocked owner lane.",
                    "blocked_by": [],
                    "recommended_focus": ["T-PUSH"],
                    "reassignment_actions": [
                        {
                            "task_id": "T-PUSH",
                            "role": "owner",
                            "from": "Gemini2",
                            "to": "Codex",
                            "reason": "Gemini2 PR push is blocked by authentication failure; Codex is an available fallback.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "provider_guardrails": {
                "task_failure_streaks": {
                    "T-PUSH:gemini2": {
                        "task_id": "T-PUSH",
                        "provider": "gemini2",
                        "count": 3,
                    }
                }
            },
            "chair_rotation": {
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Claude",
            },
        }
        status = {
            "tasks": [
                {
                    "id": "T-PUSH",
                    "status": "blocked",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "waiting_for": "Gemini",
                    "next": "PR push blocked by auth failure.",
                }
            ],
            "blockers": [
                {
                    "task_id": "T-PUSH",
                    "owner": "Gemini2",
                    "waiting_for": "Gemini",
                    "status": "open",
                }
            ],
        }

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist_task_reassignment,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["chair_rotation"]["last_review_reassignment_actions"][0]["to"], "Codex")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})
        persist_task_reassignment.assert_called_once_with(
            self.config,
            task_id="T-PUSH",
            new_owner="Codex",
            new_reviewer="Claude",
            message=(
                "Chair reassigned owner from Gemini2 to Codex: Gemini2 PR push is blocked by authentication "
                "failure; Codex is an available fallback. Task returned to todo for a blocked-owner rescue dispatch."
            ),
            new_status="todo",
            handoff_to="Codex",
            handoff_from="Gemini2",
            resolve_open_blockers=True,
        )

    def test_chair_review_prompt_includes_pending_approval_details(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        with (
            mock.patch.object(
                supervisor,
                "safe_load_approval_state",
                return_value={
                    "pending": [
                        {
                            "approval_id": "apr-1",
                            "provider": "claude",
                            "task_id": "SVC-GOVERNANCE-API",
                            "worker_run_id": "run-1",
                            "tool_name": "Bash",
                            "risk_class": "needs_review",
                            "created_at": "2026-04-28T12:00:00Z",
                            "tool_input_preview": "docker compose config --quiet",
                        }
                    ]
                },
            ),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
        ):
            message = supervisor.build_chair_review_message(self.config, {}, agent_name="Codex", review_path=review_path)

        self.assertIn("Pending Approval Details:", message)
        self.assertIn("approval_id=apr-1", message)
        self.assertIn("docker compose config --quiet", message)
        self.assertIn('"approval_actions"', message)

    def test_chair_review_prompt_includes_blocked_owner_rescue_candidates(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        status = {
            "tasks": [
                {
                    "id": "T-PUSH",
                    "status": "blocked",
                    "owner": "Gemini2",
                    "reviewer": "Claude",
                    "waiting_for": "Gemini",
                    "next": "PR push blocked by auth failure.",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "safe_load_approval_state", return_value={"pending": []}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "load_status", return_value=status),
        ):
            message = supervisor.build_chair_review_message(self.config, {}, agent_name="Codex", review_path=review_path)

        self.assertIn("Blocked Owner Rescue Candidates:", message)
        self.assertIn("task=T-PUSH", message)
        self.assertIn('targets=["Codex", "Codex2", "Claude2"]', message)

    def test_persist_task_reassignment_can_clear_blocked_owner_handoff(self) -> None:
        status_path = self.root / "ai-status.json"
        status_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "T-PUSH",
                            "status": "blocked",
                            "owner": "Gemini2",
                            "reviewer": "Claude",
                            "waiting_for": "Gemini",
                            "next": "PR push blocked by auth failure.",
                        }
                    ],
                    "blockers": [
                        {
                            "task_id": "T-PUSH",
                            "owner": "Gemini2",
                            "waiting_for": "Gemini",
                            "status": "open",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with (
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-28T12:15:00Z"),
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
        ):
            applied = supervisor.persist_task_reassignment(
                self.config,
                task_id="T-PUSH",
                new_owner="Codex",
                new_reviewer="Claude",
                message="Chair reassigned owner from Gemini2 to Codex.",
                new_status="todo",
                handoff_to="Codex",
                handoff_from="Gemini2",
                resolve_open_blockers=True,
            )

        self.assertTrue(applied)
        saved = json.loads(status_path.read_text(encoding="utf-8"))
        task = saved["tasks"][0]
        blocker = saved["blockers"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["owner"], "Codex")
        self.assertNotIn("waiting_for", task)
        self.assertEqual(blocker["status"], "resolved")
        self.assertEqual(blocker["resolution_ref"], "chair_reassignment:T-PUSH")

    def test_refresh_chair_review_invalid_decision_retries_next_chair(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text("# Summary\n\nMalformed decision.\n", encoding="utf-8")
        decision_path.write_text('{"version": 1, "decision": "maybe"}\n', encoding="utf-8")
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {},
            "chair_rotation": {
                "last_chair_run_at": "2026-04-28T12:00:00Z",
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["last_chair_run_at"])
        self.assertFalse(rotation["last_review_valid"])
        self.assertEqual(rotation["last_chair_problem"], "chair_review_invalid_schema")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_invalid_schema")

    def test_refresh_chair_review_missing_report_retries_next_chair(self) -> None:
        review_path = self.root / "chair-reviews" / "20260428-codex.md"
        decision_path = review_path.with_suffix(".json")
        state = {
            "queue": {"events": {"evt-1": {"status": "completed"}}},
            "workers": {
                "chair-1": {
                    "run_id": "chair-1",
                    "agent_id": "codex",
                    "provider": "codex",
                    "task_id": None,
                    "status": "completed",
                    "queue_event_id": "evt-1",
                    "request_snapshot": {
                        "reason": "chair_review:operational_review",
                        "metadata": {
                            "chair": {
                                "mode": "chair_review",
                                "review_path": str(review_path),
                                "decision_path": str(decision_path),
                            }
                        },
                    },
                }
            },
            "chair_rotation": {
                "last_chair_run_at": "2026-04-28T12:00:00Z",
                "pending_review_path": str(review_path),
                "pending_decision_path": str(decision_path),
                "pending_review_event_id": "evt-1",
                "pending_review_agent": "Codex",
            },
        }

        with mock.patch.object(supervisor, "write_activity_log") as write_activity_log:
            changed = supervisor.refresh_chair_review_state(self.config, state)

        self.assertTrue(changed)
        rotation = state["chair_rotation"]
        self.assertIsNone(rotation["pending_review_path"])
        self.assertIsNone(rotation["last_chair_run_at"])
        self.assertEqual(rotation["last_chair_problem"], "chair_review_missing_report")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "chair_review_missing_report")


class DeferredApprovalCorrelationTests(unittest.TestCase):
    task_id = "ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001"
    command = "git commit -F /tmp/odp-secret-schema-msg.txt 2>&1 | tail -20"

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "approval_queue": str(self.root / "approval-queue.json"),
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity-log.jsonl"),
                "evidence_dir": str(self.root / "evidence"),
                "status_file": str(self.root / "ai-status.json"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex6": {"id": "codex6", "display_name": "Codex6"},
            },
        }
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        (self.root / "approval-queue.json").write_text(
            json.dumps({"pending": [], "history": []}),
            encoding="utf-8",
        )
        self.status = {
            "tasks": [
                {
                    "id": self.task_id,
                    "status": "in_progress",
                    "owner": "Claude2",
                    "reviewer": "Codex6",
                }
            ]
        }

    def _write_deferred_log(self) -> Path:
        log_path = self.root / "claude2.log"
        receipt = {
            "is_error": False,
            "stop_reason": "tool_deferred",
            "session_id": "7d919ae4-893a-400b-b13f-b45e5115184b",
            "terminal_reason": "tool_deferred",
            "deferred_tool_use": {
                "id": "toolu_01YZMtMekPkN7JQUG6AdSo1f",
                "name": "Bash",
                "input": {
                    "command": self.command,
                    "description": "Create task commit",
                },
            },
            "type": "result",
        }
        log_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        return log_path

    def _state_for_deferred_log(self) -> dict[str, object]:
        return {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": self.task_id,
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "log_path": str(self._write_deferred_log()),
                    "last_event_at": "2026-07-28T16:48:00Z",
                }
            },
        }

    def _poll(self, state: dict[str, object], broker_decision: dict[str, str]) -> bool:
        with (
            mock.patch.object(
                supervisor,
                "load_approval_state",
                return_value={"pending": [], "history": []},
            ),
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "_deferred_tool_broker_decision",
                return_value=broker_decision,
            ),
        ):
            return supervisor.poll_workers(self.config, state)

    def test_normal_poll_makes_claude2_receipt_durable_before_dead_worker_cleanup(self) -> None:
        state = self._state_for_deferred_log()

        changed = self._poll(
            state,
            {"decision": "defer", "risk_class": "git_write"},
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["session_id"], "7d919ae4-893a-400b-b13f-b45e5115184b")
        self.assertEqual(worker["deferred_tool_use"]["input"]["command"], self.command)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(len(approval_state["pending"]), 1)
        approval = approval_state["pending"][0]
        self.assertEqual(approval["worker_run_id"], "run-1")
        self.assertEqual(approval["tool_use_id"], "toolu_01YZMtMekPkN7JQUG6AdSo1f")
        self.assertEqual(approval["suggested_rule"], f"Bash({self.command})")
        self.assertEqual(worker["deferred_action"], approval["approval_id"])

    def test_boot_reconciliation_correlates_flushed_receipt_before_missing_process_failure(self) -> None:
        state = self._state_for_deferred_log()

        with (
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "_deferred_tool_broker_decision",
                return_value={"decision": "defer", "risk_class": "git_write"},
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "waiting_approval")
        self.assertEqual(worker["session_id"], "7d919ae4-893a-400b-b13f-b45e5115184b")
        self.assertEqual(worker["deferred_tool_use"]["id"], "toolu_01YZMtMekPkN7JQUG6AdSo1f")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(len(approval_state["pending"]), 1)
        self.assertEqual(approval_state["pending"][0]["worker_run_id"], "run-1")
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertNotIn("worker_failed", activity_types)
        metrics = state.get("worker_runtime_metrics", {})
        self.assertEqual(metrics.get("totals", {}).get("missing_process_workers_failed", 0), 0)

    def test_boot_reconciliation_fails_closed_when_receipt_cannot_be_persisted(self) -> None:
        state = self._state_for_deferred_log()

        with (
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "correlate_deferred_tool_approval",
                side_effect=OSError("approval queue unavailable"),
            ),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertIn("process missing", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(supervisor.load_approval_state(self.config)["pending"], [])
        activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
        self.assertIn("worker_deferred_approval_failed", activity_types)
        self.assertIn("worker_failed", activity_types)
        metrics = state["worker_runtime_metrics"]
        self.assertEqual(metrics["totals"]["missing_process_workers_failed"], 1)

    def test_allowed_scoped_commit_resumes_without_broad_bash(self) -> None:
        state = self._state_for_deferred_log()
        self._poll(state, {"decision": "defer", "risk_class": "git_write"})
        pending = supervisor.load_approval_state(self.config)["pending"][0]
        resolved = supervisor.resolve_approval(
            self.config,
            pending["approval_id"],
            decision="allow",
            note="Allow only the staged task commit.",
            remember=False,
        )
        resolved_state = supervisor.load_approval_state(self.config)

        def resume(
            _config: dict[str, object],
            worker: dict[str, object],
            _provider_report: dict[str, object],
            *,
            approval: dict[str, object],
        ) -> dict[str, object]:
            worker["status"] = "running"
            worker["deferred_tool_use"] = None
            return {
                "command": ["claude", "--resume", str(worker["session_id"])],
                "allowed_tools": supervisor._claude_resume_allowed_tools(approval),
            }

        with (
            mock.patch.object(
                supervisor,
                "load_approval_state",
                return_value=resolved_state,
            ),
            mock.patch.object(supervisor, "load_status", return_value=self.status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(
                supervisor,
                "resume_claude_worker",
                side_effect=resume,
            ) as resume_claude_worker,
        ):
            changed = supervisor.poll_workers(self.config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        resume_claude_worker.assert_called_once()
        resumed_approval = resume_claude_worker.call_args.kwargs["approval"]
        self.assertEqual(resumed_approval["approval_id"], resolved["approval_id"])
        self.assertEqual(
            supervisor._claude_resume_allowed_tools(resumed_approval),
            [f"Bash({self.command})"],
        )
        self.assertNotIn("Bash", supervisor._claude_resume_allowed_tools(resumed_approval))

    def test_broker_denied_deferred_tool_fails_closed(self) -> None:
        state = self._state_for_deferred_log()

        changed = self._poll(
            state,
            {
                "decision": "deny",
                "risk_class": "forbidden",
                "reason": "Command is forbidden by policy.",
            },
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        approval_state = supervisor.load_approval_state(self.config)
        self.assertEqual(approval_state["pending"], [])
        self.assertEqual(approval_state["history"][0]["decision"], "deny")
        self.assertEqual(approval_state["history"][0]["note"], "Command is forbidden by policy.")

    def test_missing_deferred_receipt_still_fails_closed(self) -> None:
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": self.task_id,
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "suspended_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "session-without-receipt",
                    "last_event_at": "2026-07-28T16:48:00Z",
                }
            },
        }

        changed = self._poll(
            state,
            {"decision": "defer", "risk_class": "git_write"},
        )

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(
            worker["last_error"],
            "Approval state disappeared before the suspended worker could resume.",
        )
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")


class PollWorkersRecoveryTests(unittest.TestCase):
    def test_successful_chair_worker_does_not_scan_report_text_as_provider_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "chair.log"
            log_path.write_text(
                "+   - ASST-OCGW-004 recorded `codex1_1` auth failures with `not authenticated, please login first`.\n",
                encoding="utf-8",
            )
            config = {
                "schema": {"tasks_path": "tasks"},
                "supervisor": {"stall_after_seconds": 300},
                "ready_dispatcher": {
                    "active_worker_statuses": [
                        "running",
                        "started",
                        "waiting_approval",
                        "manual_pending",
                        "retry_backoff",
                        "suspended_approval",
                        "stalled",
                        "fallback",
                    ],
                    "worker_terminal_statuses": ["done", "review_approved", "review"],
                },
                "providers": {},
                "agents": {"codex2": {"id": "codex2", "display_name": "Codex2", "provider": "codex2"}},
            }
            state = {
                "queue": {"events": {"evt-chair": {"status": "started"}}},
                "workers": {
                    "chair-run": {
                        "run_id": "chair-run",
                        "provider": "codex2-1",
                        "agent_id": "codex2_1",
                        "task_id": None,
                        "status": "running",
                        "queue_event_id": "evt-chair",
                        "pid": 12345,
                        "log_path": str(log_path),
                        "runner_status": "completed",
                        "exit_code": 0,
                        "request_snapshot": {
                            "reason": "chair_review:reassignment_triage",
                            "metadata": {"chair": {"mode": "chair_review", "review_path": "chair-reviews/20260428-codex2.md"}},
                        },
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
                mock.patch.object(supervisor, "load_provider_report", return_value={}),
                mock.patch.object(supervisor, "retry_due_workers", return_value=False),
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "detect_worker_failure", side_effect=AssertionError("should not scan successful chair log")),
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.poll_workers(config, state)

            self.assertTrue(changed)
            self.assertEqual(state["workers"]["chair-run"]["status"], "completed")
            self.assertEqual(state["queue"]["events"]["evt-chair"]["status"], "completed")
            mark_provider_dispatch_paused.assert_not_called()
            self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_completed")

    def test_lower_priority_worker_is_superseded_when_finalize_backlog_exists(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2026-04-06T09:00:00Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex", "depends_on": []},
                {"id": "EX-001", "status": "review_approved", "approved_head": "1111111122222222333333334444444455555555", "owner": "Copilot", "reviewer": "Claude", "depends_on": []},
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"),
            mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")),
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "superseded")
        self.assertIn("prioritize higher-priority review/finalize work", worker["last_error"])
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(12345)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_parent_worker_is_not_superseded_for_its_sidecar_review(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "dependency_done_statuses": ["done"],
                "active_worker_statuses": ["running", "started", "waiting_approval", "manual_pending", "retry_backoff", "suspended_approval", "stalled", "fallback"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
                "claude": {"id": "claude", "display_name": "Claude"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "BP5-SVC-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 12345,
                    "last_event_at": "2099-04-15T15:29:37Z",
                    "request_snapshot": {"reason": "owned_ready_dispatch"},
                }
            },
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                    "depends_on": [],
                },
                {
                    "id": "BP5-SVC-001-SIDECAR-ACCEPTANCE",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "depends_on": [],
                    "task_class": "sidecar",
                    "auto_generated": True,
                    "helper_parent": "BP5-SVC-001",
                    "helper_kind": "acceptance_packet",
                },
            ]
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertIsInstance(changed, bool)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "running")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "started")
        terminate_worker_pid.assert_not_called()
        write_activity_log.assert_not_called()

    def test_dead_worker_for_open_task_is_marked_failed_not_completed(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "detect_worker_failure", return_value=None),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], supervisor.NO_PROGRESS_WORKER_EXIT_REASON)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_waiting_approval_worker_is_failed_and_approval_is_resolved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {},
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "OC-002", "status": "review", "owner": "Codex", "reviewer": "Claude"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "OC-002",
                    "provider": "claude",
                    "tool_name": "Bash",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "failed")
        self.assertEqual(worker["last_error"], "Worker exited while waiting for approval.")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        resolve_approval.assert_called_once_with(
            config,
            "apr-1",
            decision="deny",
            note="Auto-denied because the worker exited before approval could be applied.",
            remember=False,
        )
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_dead_claude_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {},
            "agents": {
                "claude": {"id": "claude", "display_name": "Claude"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "agent_id": "claude",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-123",
                    "resume_token": "sess-123",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-004", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-1",
                    "worker_run_id": "run-1",
                    "task_id": "LP-004",
                    "provider": "claude",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-1")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_claude2_waiting_approval_worker_with_session_is_suspended(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": [
                    "running",
                    "waiting_approval",
                    "suspended_approval",
                    "manual_pending",
                ]
            },
            "providers": {"claude2": {"delivery_mode": "claude_cli"}},
            "agents": {
                "claude2": {"id": "claude2", "display_name": "Claude2"},
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "agent_id": "claude2",
                    "status": "waiting_approval",
                    "queue_event_id": "evt-1",
                    "pid": 999999,
                    "session_id": "sess-456",
                    "resume_token": "sess-456",
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-005", "status": "in_progress", "owner": "Claude2", "reviewer": "Codex"}]}
        approval_state = {
            "pending": [
                {
                    "approval_id": "apr-2",
                    "worker_run_id": "run-1",
                    "task_id": "LP-005",
                    "provider": "claude2",
                    "tool_name": "ToolSearch",
                    "created_at": "2026-04-06T09:01:00Z",
                }
            ],
            "history": [],
        }

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value=approval_state),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_approval") as resolve_approval,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-1"]
        self.assertEqual(worker["status"], "suspended_approval")
        self.assertEqual(worker["deferred_action"], "apr-2")
        self.assertEqual(worker["last_event_at"], "2026-04-06T09:01:00Z")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "manual_pending")
        resolve_approval.assert_not_called()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_waiting_approval")

    def test_dead_stale_worker_is_reaped_when_task_assignment_moved(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "name": "Codex"},
                "claude": {"id": "claude", "name": "Claude"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "manual_pending"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "EX-001",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "manual_pending",
                    "queue_event_id": "evt-1",
                    "pid": None,
                    "last_event_at": "2026-04-06T09:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "EX-001", "status": "review", "owner": "Grok", "reviewer": "Claude"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_stalled_worker_returns_to_running_after_new_log_activity(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "LP-002",
                    "provider": "codex",
                    "agent_id": "codex",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:20:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "LP-002", "status": "in_progress", "owner": "Codex", "reviewer": "Copilot"}]}

        def bump_log_activity(_config, worker):
            worker["last_event_at"] = "2026-04-06T14:31:28Z"

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=bump_log_activity),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "running")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_recovered")

    def test_stalled_worker_is_terminated_after_extended_stall(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["todo", "in_progress"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "FB-003",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 1234,
                    "last_event_at": "2026-04-06T14:00:00Z",
                }
            },
        }
        status = {"tasks": [{"id": "FB-003", "status": "todo", "owner": "Copilot", "reviewer": "Codex"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "update_from_log", side_effect=lambda *_args, **_kwargs: None),
            mock.patch.object(supervisor, "terminate_worker_pid") as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "failed")
        terminate_worker_pid.assert_called_once_with(1234)
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "failed")
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_failed")

    def test_alive_worker_is_superseded_after_reassignment(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "owned_statuses": ["in_progress", "todo"],
                "done_statuses": ["done", "review_approved"],
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {
                "copilot": {"id": "copilot", "display_name": "Copilot"},
                "gemini": {"id": "gemini", "display_name": "Gemini"},
            },
        }
        state = {
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "task_id": "REG-002",
                    "provider": "copilot",
                    "agent_id": "copilot",
                    "status": "stalled",
                    "queue_event_id": "evt-1",
                    "pid": 2222,
                    "last_event_at": "2026-04-06T14:19:47Z",
                }
            },
        }
        status = {"tasks": [{"id": "REG-002", "status": "review", "owner": "Codex", "reviewer": "Gemini"}]}

        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        self.assertEqual(state["workers"]["run-1"]["status"], "superseded")
        self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(2222)
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "worker_superseded")

    def test_alive_chair_worker_is_completed_after_valid_artifacts_apply(self) -> None:
        config = {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "supervisor": {"stall_after_seconds": 300},
            "ready_dispatcher": {
                "active_worker_statuses": ["running", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled"],
            },
            "providers": {},
            "agents": {"codex2": {"id": "codex2", "display_name": "Codex2"}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            review_path = root / "20260601-125122-codex2.md"
            decision_path = root / "20260601-125122-codex2.json"
            review_path.write_text("# Review\n", encoding="utf-8")
            decision_path.write_text('{"version":1}\n', encoding="utf-8")
            state = {
                "queue": {"events": {"evt-chair": {"status": "started"}}},
                "chair_rotation": {
                    "last_review_path": str(review_path),
                    "last_review_decision_path": str(decision_path),
                    "last_review_valid": True,
                },
                "workers": {
                    "run-chair": {
                        "run_id": "run-chair",
                        "task_id": None,
                        "provider": "codex2-1",
                        "agent_id": "codex2_1",
                        "status": "running",
                        "queue_event_id": "evt-chair",
                        "pid": 4242,
                        "last_event_at": "2026-06-01T12:58:00Z",
                        "request_snapshot": {
                            "reason": "chair_review:reassignment_triage",
                            "metadata": {"chair": {"review_path": str(review_path)}},
                        },
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
                mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
                mock.patch.object(supervisor, "load_provider_report", return_value={}),
                mock.patch.object(supervisor, "retry_due_workers", return_value=False),
                mock.patch.object(supervisor, "pid_is_alive", return_value=True),
                mock.patch.object(supervisor, "terminate_worker_pid", return_value=True) as terminate_worker_pid,
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
                mock.patch.object(supervisor, "utc_now", return_value="2026-06-01T12:59:30Z"),
            ):
                changed = supervisor.poll_workers(config, state)

        self.assertTrue(changed)
        worker = state["workers"]["run-chair"]
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["last_event_at"], "2026-06-01T12:59:30Z")
        self.assertEqual(state["queue"]["events"]["evt-chair"]["status"], "completed")
        terminate_worker_pid.assert_called_once_with(4242)
        payload = write_activity_log.call_args.args[1]
        self.assertEqual(payload["type"], "worker_completed")
        self.assertIn("artifacts were accepted", payload["message"])


class SingleSupervisorGuardTests(unittest.TestCase):
    def test_cmdline_match_requires_supervisor_as_executable_or_python_script(self) -> None:
        script = str(Path(supervisor.__file__).resolve())

        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", ".orchestrator/supervisor.py", "--verbose"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process(["python3", script, "--poll-interval", "15"]))
        self.assertTrue(supervisor.cmdline_is_supervisor_process([".orchestrator/supervisor.py", "--once"]))

    def test_cmdline_match_ignores_wrapper_processes(self) -> None:
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["timeout", "20s", "python3", ".orchestrator/supervisor.py", "--once"])
        )
        self.assertFalse(
            supervisor.cmdline_is_supervisor_process(["bash", "-lc", "python3 .orchestrator/supervisor.py --verbose"])
        )

    def test_terminate_other_supervisors_kills_all_matching_except_self(self) -> None:
        # Singleton semantics: the flock winner terminates every other matching
        # supervisor regardless of PID ordering. 404 > 202 must still be killed
        # (PID wraparound previously let a higher-PID older supervisor survive).
        config = {"activity_log": "/tmp/fake-log.jsonl"}
        killed: list[tuple[int, int]] = []
        alive = {101: True, 202: True, 404: True}

        def fake_kill(pid: int, sig: int) -> None:
            killed.append((pid, sig))
            if sig in {supervisor.signal.SIGTERM, supervisor.signal.SIGKILL}:
                alive[pid] = False

        with (
            mock.patch.object(supervisor, "iter_matching_supervisor_pids", return_value=[101, 202, 404]),
            mock.patch.object(supervisor, "pid_is_alive", side_effect=lambda pid: alive.get(pid, False)),
            mock.patch.object(supervisor.os, "getpid", return_value=202),
            mock.patch.object(supervisor.os, "kill", side_effect=fake_kill),
            mock.patch.object(supervisor.time, "sleep"),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            supervisor.terminate_other_supervisors(config)

        self.assertEqual(
            killed,
            [(101, supervisor.signal.SIGTERM), (404, supervisor.signal.SIGTERM)],
        )
        self.assertEqual(write_activity_log.call_count, 2)
        terminated_pids = {
            call.args[1]["old_pid"] for call in write_activity_log.call_args_list
        }
        self.assertEqual(terminated_pids, {101, 404})
        for call in write_activity_log.call_args_list:
            self.assertEqual(call.args[1]["type"], "supervisor_replaced")
            self.assertEqual(call.args[1]["new_pid"], 202)

    def test_singleton_lock_is_exclusive_and_released_on_close(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = {"paths": {"state_file": str(Path(tmp) / "runtime-state.json")}}

            # First acquirer wins.
            self.assertTrue(supervisor.acquire_singleton_lock(config))
            first_handle = supervisor._SINGLETON_LOCK_HANDLE
            self.assertIsNotNone(first_handle)
            # pid file content reflects the owner.
            self.assertEqual(
                supervisor.supervisor_lock_path(config).read_text(encoding="utf-8").strip(),
                str(supervisor.os.getpid()),
            )

            # A concurrent acquirer (separate fd) is refused while the lock is held.
            import fcntl as _fcntl

            contender = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                with self.assertRaises(OSError):
                    _fcntl.flock(
                        contender.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB
                    )
            finally:
                contender.close()

            # Releasing (process exit simulated by closing the fd) frees the lock.
            first_handle.close()
            regained = open(supervisor.supervisor_lock_path(config), "a+", encoding="utf-8")
            try:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            finally:
                _fcntl.flock(regained.fileno(), _fcntl.LOCK_UN)
                regained.close()


class WorktreeDirtClassificationTests(unittest.TestCase):
    def test_clean_status(self) -> None:
        self.assertEqual(supervisor._classify_worktree_dirt(""), ("clean", []))
        self.assertEqual(supervisor._classify_worktree_dirt("\n  \n"), ("clean", []))

    def test_scratch_only_is_reusable(self) -> None:
        # Untracked scratch/context paths are classified as scratch_only.
        status = (
            "?? .orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md\n"
            "?? .orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "scratch_only")
        self.assertEqual(
            set(paths),
            {
                ".orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md",
                ".orchestrator/reviews/mgmt_ai_persist_p1_attach_007_review.md",
            },
        )

    def test_tracked_or_staged_context_dirt_classified_as_real(self) -> None:
        # Tracked/staged modifications under context or scratch paths are real dirt.
        status = (
            "MM .orchestrator/task-briefs/mgmt_ai_persist_p1_attach_007.md\n"
            " M ai-status.json\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")
        self.assertEqual(paths, [])

    def test_real_product_dirt_still_blocks(self) -> None:
        status = (
            " M .orchestrator/task-briefs/asst_integ_004.md\n"
            " M services/control-plane/bff/main.py\n"
        )
        kind, paths = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")
        self.assertEqual(paths, [])

    def test_rename_uses_new_path(self) -> None:
        status = "R  old/file.py -> services/new/file.py\n"
        kind, _ = supervisor._classify_worktree_dirt(status)
        self.assertEqual(kind, "real")


class WorktreeLeaseBlockEscalationTests(unittest.TestCase):
    """A block that repeats unchanged forever has to stop reading as noise."""

    def _record(self, config: dict, state: dict, events: list, *, n: int, status: str = "task_head_mismatch: local=a remote=b") -> int:
        count = 0
        with mock.patch.object(supervisor, "write_activity_log", side_effect=lambda _c, e: events.append(e)):
            for _ in range(n):
                count = supervisor._record_worktree_lease_block(
                    config,
                    state,
                    task_id="ODP-ORCH-EXAMPLE-001",
                    refresh_status=status,
                    message="reused worktree ... failed the fail-closed refresh policy",
                )
        return count

    def test_repeated_identical_blocks_escalate_exactly_once(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        count = self._record(config, state, events, n=10)

        self.assertEqual(count, 10)
        escalations = [e for e in events if e["type"] == "dispatch_blocked_worktree_lease_escalated"]
        # Once, not ten times: the point is to surface the stall, not to become
        # a second copy of the noise it is reporting.
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0]["consecutive_blocks"], 3)
        self.assertEqual(escalations[0]["task_id"], "ODP-ORCH-EXAMPLE-001")

    def test_blocks_below_the_threshold_stay_quiet(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 5}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=4)

        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])

    def test_a_different_block_reason_restarts_the_count(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=2, status="task_head_mismatch: local=a remote=b")
        count = self._record(config, state, events, n=1, status="unverifiable_refs: remote task branch is missing")

        self.assertEqual(count, 1)
        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])

    def test_a_successful_lease_clears_the_streak(self) -> None:
        config: dict = {"worker_runtime": {"lease_block_escalate_after": 3}}
        state: dict = {}
        events: list = []

        self._record(config, state, events, n=2)
        supervisor._clear_worktree_lease_block(state, "ODP-ORCH-EXAMPLE-001")
        count = self._record(config, state, events, n=1)

        self.assertEqual(count, 1)
        self.assertEqual([e for e in events if e["type"].endswith("_escalated")], [])


class ReusedWorkerWorktreeBaseAdvanceTests(unittest.TestCase):
    """Regression matrix for the clean divergence topology observed on PR #562."""

    task_branch = "task/ODP-CI-DEV-MERGE-RELEASE-NOGO-DEADLOCK-001"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        seed = root / "seed"
        self.repo_root = root / "supervisor"
        self.worktree = root / "worker"

        self._git(root, "init", "--bare", str(self.origin))
        self._git(root, "init", "-b", "dev", str(seed))
        self._git(seed, "config", "user.name", "Supervisor Test")
        self._git(seed, "config", "user.email", "supervisor-test@example.invalid")
        (seed / "tracked.txt").write_text("initial\n", encoding="utf-8")
        self._git(seed, "add", "tracked.txt")
        self._git(seed, "commit", "-m", "initial")
        self.initial_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()
        self._git(seed, "remote", "add", "origin", str(self.origin))
        self._git(seed, "push", "-u", "origin", "dev")
        self._git(self.origin, "symbolic-ref", "HEAD", "refs/heads/dev")

        self._git(seed, "switch", "-c", self.task_branch)
        (seed / "task.txt").write_text("task commit\n", encoding="utf-8")
        self._git(seed, "add", "task.txt")
        self._git(seed, "commit", "-m", "task change")
        self._git(seed, "push", "-u", "origin", self.task_branch)
        self.task_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()

        self._git(seed, "switch", "dev")
        (seed / "base.txt").write_text("advanced dev\n", encoding="utf-8")
        self._git(seed, "add", "base.txt")
        self._git(seed, "commit", "-m", "advance dev")
        self._git(seed, "push", "origin", "dev")
        self.base_head = self._git(seed, "rev-parse", "HEAD").stdout.strip()

        self._git(root, "clone", "--branch", "dev", str(self.origin), str(self.repo_root))
        self._git(
            self.repo_root,
            "worktree",
            "add",
            "-b",
            self.task_branch,
            str(self.worktree),
            f"origin/{self.task_branch}",
        )
        self._git(self.worktree, "config", "user.name", "Supervisor Test")
        self._git(self.worktree, "config", "user.email", "supervisor-test@example.invalid")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )

    def _refresh(self) -> tuple[bool, str]:
        return supervisor._refresh_reused_worker_worktree(
            self.repo_root,
            self.worktree,
            "origin/dev",
            self.task_branch,
        )

    def test_clean_matching_task_head_diverged_from_dev_dispatches_for_owner_rebase(self) -> None:
        before = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_advance_rebase_required:"), status)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), before)
        self.assertEqual(before, self.task_head)

    def test_task_containing_current_base_is_left_untouched(self) -> None:
        self._git(self.worktree, "merge", "--no-edit", "origin/dev")
        self._git(self.worktree, "push", "origin", f"HEAD:{self.task_branch}")
        composed_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_present_at_"), status)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), composed_head)

    def test_task_behind_current_base_fast_forwards(self) -> None:
        self._git(self.worktree, "reset", "--hard", self.initial_head)
        self._git(self.repo_root, "push", "origin", "--delete", self.task_branch)

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertEqual(status, f"ff_to_{self.base_head[:12]}")
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), self.base_head)

    def test_dirty_tracked_state_blocks_without_discarding_it(self) -> None:
        dirty_path = self.worktree / "task.txt"
        dirty_path.write_text("uncommitted owner work\n", encoding="utf-8")
        before_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertEqual(status, "skipped_dirty_worktree")
        self.assertEqual(dirty_path.read_text(encoding="utf-8"), "uncommitted owner work\n")
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), before_head)

    def test_dirty_tracked_orchestrator_scratch_also_blocks(self) -> None:
        scratch = self.worktree / ".orchestrator" / "config.json"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("tracked context\n", encoding="utf-8")
        self._git(self.worktree, "add", str(scratch.relative_to(self.worktree)))
        self._git(self.worktree, "commit", "-m", "track task context")
        self._git(self.worktree, "push", "origin", f"HEAD:{self.task_branch}")
        scratch.write_text("owner annotation\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertEqual(status, "skipped_dirty_worktree")
        self.assertEqual(scratch.read_text(encoding="utf-8"), "owner annotation\n")

    def _origin_task_head(self) -> str:
        result = self._git(self.origin, "rev-parse", self.task_branch, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def test_publishing_an_unpublished_commit_makes_the_lease_verifiable(self) -> None:
        """The 2026-08-05 deadlock: a committed-but-unpushed anchor blocks its own task.

        Leasing is what would run the worker that would push, and leasing is
        exactly what the fail-closed policy refuses. Publishing breaks the cycle
        by producing the local==remote state the policy already accepts.
        """

        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "anchor commit that was never pushed")
        local_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        # Precondition: the policy blocks this, which is what stalls the fleet.
        blocked_ok, blocked_status = self._refresh()
        self.assertFalse(blocked_ok)
        self.assertTrue(blocked_status.startswith("task_head_mismatch:"), blocked_status)

        published, detail = supervisor._publish_unpublished_task_branch(self.worktree, self.task_branch)

        self.assertTrue(published, detail)
        self.assertEqual(self._origin_task_head(), local_head)
        # And the same policy now passes, without its rules having been relaxed.
        self.assertTrue(self._refresh()[0])

    def test_publishing_creates_a_task_branch_that_was_never_pushed_at_all(self) -> None:
        self._git(self.origin, "update-ref", "-d", f"refs/heads/{self.task_branch}")
        self._git(self.worktree, "fetch", "--prune", "origin", check=False)
        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "anchor on an unpublished branch")
        local_head = self._git(self.worktree, "rev-parse", "HEAD").stdout.strip()

        published, detail = supervisor._publish_unpublished_task_branch(self.worktree, self.task_branch)

        self.assertTrue(published, detail)
        self.assertEqual(self._origin_task_head(), local_head)

    def test_dirty_worktree_is_never_published(self) -> None:
        """Dispatch must not publish working-tree state nobody committed."""

        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "anchor commit")
        (self.worktree / "scratch.txt").write_text("uncommitted owner note\n", encoding="utf-8")
        self._git(self.worktree, "add", "scratch.txt")
        before = self._origin_task_head()

        published, detail = supervisor._publish_unpublished_task_branch(self.worktree, self.task_branch)

        self.assertFalse(published)
        self.assertIn("not clean", detail)
        self.assertEqual(self._origin_task_head(), before)

    def test_genuinely_diverged_branch_is_never_published(self) -> None:
        """Ahead *and* behind needs a rebase decision, not a push."""

        # Someone else advances the published task branch.
        sibling = Path(self.tmp.name) / "sibling"
        self._git(Path(self.tmp.name), "clone", "--branch", self.task_branch, str(self.origin), str(sibling))
        self._git(sibling, "config", "user.name", "Other Worker")
        self._git(sibling, "config", "user.email", "other@example.invalid")
        (sibling / "remote-only.txt").write_text("remote\n", encoding="utf-8")
        self._git(sibling, "add", "remote-only.txt")
        self._git(sibling, "commit", "-m", "remote side commit")
        self._git(sibling, "push", "origin", self.task_branch)
        remote_head = self._origin_task_head()

        # Meanwhile this worktree commits its own work.
        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "local side commit")
        self._git(self.worktree, "fetch", "origin", self.task_branch)

        published, detail = supervisor._publish_unpublished_task_branch(self.worktree, self.task_branch)

        self.assertFalse(published)
        self.assertIn("diverged", detail)
        self.assertEqual(self._origin_task_head(), remote_head)

    def test_local_and_remote_task_head_mismatch_blocks(self) -> None:
        (self.worktree / "local-only.txt").write_text("local\n", encoding="utf-8")
        self._git(self.worktree, "add", "local-only.txt")
        self._git(self.worktree, "commit", "-m", "local only")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("task_head_mismatch:"), status)

    def test_clean_local_task_behind_remote_fast_forwards_to_published_head(self) -> None:
        self._git(self.repo_root, "fetch", "origin", self.task_branch)
        self._git(self.repo_root, "switch", "--detach", f"origin/{self.task_branch}")
        self._git(self.repo_root, "config", "user.name", "Supervisor Test")
        self._git(self.repo_root, "config", "user.email", "supervisor-test@example.invalid")
        (self.repo_root / "published.txt").write_text("published task update\n", encoding="utf-8")
        self._git(self.repo_root, "add", "published.txt")
        self._git(self.repo_root, "commit", "-m", "publish task update")
        self._git(self.repo_root, "push", "origin", f"HEAD:{self.task_branch}")
        published_head = self._git(self.repo_root, "rev-parse", "HEAD").stdout.strip()

        ok, status = self._refresh()

        self.assertTrue(ok, status)
        self.assertTrue(status.startswith("base_advance_rebase_required:"), status)
        self.assertEqual(self._git(self.worktree, "rev-parse", "HEAD").stdout.strip(), published_head)

    def test_wrong_branch_blocks(self) -> None:
        self._git(self.worktree, "switch", "-c", "task/WRONG-BRANCH")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("wrong_branch:"), status)

    def test_wrong_repository_worktree_blocks(self) -> None:
        other = Path(self.tmp.name) / "other"
        self._git(Path(self.tmp.name), "init", "-b", "dev", str(other))

        ok, status = supervisor._refresh_reused_worker_worktree(
            self.repo_root,
            other,
            "origin/dev",
            self.task_branch,
        )

        self.assertFalse(ok)
        self.assertTrue(status.startswith("wrong_worktree:"), status)

    def test_fetch_failure_blocks(self) -> None:
        self._git(self.worktree, "remote", "set-url", "origin", str(Path(self.tmp.name) / "missing.git"))

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("fetch_failed:"), status)

    def test_unresolved_git_operation_blocks(self) -> None:
        marker = Path(self._git(self.worktree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
        marker.write_text(self.base_head + "\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertEqual(status, "unresolved_git_operation")

    def test_unresolved_rebase_blocks(self) -> None:
        raw_rebase_head = self._git(
            self.worktree, "rev-parse", "--git-path", "REBASE_HEAD"
        ).stdout.strip()
        rebase_head = Path(raw_rebase_head)
        if not rebase_head.is_absolute():
            rebase_head = self.worktree / rebase_head
        rebase_head.write_text(self.initial_head + "\n", encoding="utf-8")

        for marker_name in ("rebase-merge", "rebase-apply"):
            with self.subTest(marker=marker_name):
                raw_marker = self._git(
                    self.worktree, "rev-parse", "--git-path", marker_name
                ).stdout.strip()
                marker = Path(raw_marker)
                if not marker.is_absolute():
                    marker = self.worktree / marker
                marker.mkdir(parents=True)

                ok, status = self._refresh()

                self.assertFalse(ok)
                self.assertEqual(status, "unresolved_git_operation")
                marker.rmdir()

    def test_stale_rebase_head_after_completed_rebase_does_not_block(self) -> None:
        raw_marker = self._git(self.worktree, "rev-parse", "--git-path", "REBASE_HEAD").stdout.strip()
        marker = Path(raw_marker)
        if not marker.is_absolute():
            marker = self.worktree / marker
        marker.write_text(self.initial_head + "\n", encoding="utf-8")

        ok, status = self._refresh()

        self.assertTrue(ok)
        self.assertTrue(status.startswith("base_advance_rebase_required:"), status)
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), self.initial_head)

    def test_unverifiable_fetched_base_blocks(self) -> None:
        with mock.patch.object(supervisor, "_git_commit_oid", side_effect=[self.task_head, None]):
            ok, status = self._refresh()

        self.assertFalse(ok)
        self.assertTrue(status.startswith("unverifiable_refs:"), status)


class WorkerReassignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_reassigns_review_task_to_new_reviewer_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "P3-001",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-001",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "P3-001")
        self.assertEqual(kwargs["new_owner"], "Claude")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertEqual(kwargs["handoff_to"], "Codex")
        write_activity_log.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_reassigned")

    def test_reassign_review_skips_paused_reviewer_candidates(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "qwen": {
                        "provider": "qwen",
                        "blocked_until": "2099-01-01T00:00:00Z",
                    }
                }
            }
        }
        worker = {
            "task_id": "P3-002",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-002",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                state,
                worker,
                "status: 401 unauthorized",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Copilot")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Copilot")

    def test_reassign_review_can_fall_back_to_codex2_when_codex_is_owner(self) -> None:
        config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "reviewer_fallbacks": {
                    "Claude": ["Codex", "Codex2", "Qwen", "Copilot", "Gemini"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude", "provider": "claude"},
                "qwen": {"display_name": "Qwen", "provider": "qwen"},
                "codex": {"display_name": "Codex", "provider": "codex"},
                "codex2": {"display_name": "Codex2", "provider": "codex2"},
                "copilot": {"display_name": "Copilot", "provider": "copilot"},
                "gemini": {"display_name": "Gemini", "provider": "gemini"},
            },
        }
        worker = {
            "task_id": "P3-003",
            "agent_id": "claude",
            "retry_count": 1,
            "run_id": "claude-run-3",
        }
        status = {
            "tasks": [
                {
                    "id": "P3-003",
                    "status": "review",
                    "owner": "Codex",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "Credit balance is too low",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Codex2")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], "Codex2")

    def test_reassigns_owned_task_to_new_owner_after_repeated_failure(self) -> None:
        worker = {
            "task_id": "LP-003",
            "agent_id": "gemini",
            "retry_count": 1,
            "run_id": "gemini-run-2",
        }
        status = {
            "tasks": [
                {
                    "id": "LP-003",
                    "status": "in_progress",
                    "owner": "Gemini",
                    "reviewer": "Claude",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                worker,
                "status: 429",
            )

        self.assertEqual(reassigned_to, "Codex")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "LP-003")
        self.assertEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Claude")
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertIn("Task returned to todo until Codex starts a fresh run.", kwargs["message"])


class WorkerPreemptionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
                "reviewer_fallbacks": {
                    "Gemini": ["Codex", "Claude", "Grok"],
                },
            },
            "agents": {
                "claude": {"display_name": "Claude"},
                "gemini": {"display_name": "Gemini"},
                "codex": {"display_name": "Codex"},
                "grok": {"display_name": "Grok"},
            },
        }

    def test_sync_preempted_owned_task_returns_in_progress_task_to_todo(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-CICD-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-CICD-001",
                    "status": "in_progress",
                    "owner": "Codex",
                    "reviewer": "Gemini",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("returned to todo until a fresh run restarts it", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_sync_preempted_finalize_task_keeps_review_approved(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex"},
            },
        }
        worker = {
            "task_id": "BP5-SVC-001",
            "agent_id": "codex",
            "provider": "codex",
            "request_snapshot": {"reason": "owned_finalize_dispatch"},
        }
        status = {
            "tasks": [
                {
                    "id": "BP5-SVC-001",
                    "status": "review_approved",
                    "owner": "Codex",
                    "reviewer": "Qwen",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "write_json") as write_json,
            mock.patch.object(supervisor, "sync_status_pipeline", return_value=True),
            mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            mock.patch.object(supervisor, "utc_now", return_value="2026-04-15T16:09:52Z"),
        ):
            synced = supervisor.sync_preempted_task_status(config, worker)

        self.assertTrue(synced)
        task = status["tasks"][0]
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["last_update"], "2026-04-15T16:09:52Z")
        self.assertIn("task remains review_approved", task["next"])
        write_json.assert_called_once()
        self.assertEqual(write_activity_log.call_args.args[1]["type"], "task_preempted_synced")

    def test_reassigns_finalize_task_to_new_owner_after_repeated_failure(self) -> None:
        config = {
            **self.config,
            "ready_dispatcher": {
                "sidecar_only_agents": ["Qwen"],
            },
            "worker_reassignment": {
                **self.config["worker_reassignment"],
                "owner_fallbacks": {
                    **self.config["worker_reassignment"]["owner_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
                "reviewer_fallbacks": {
                    **self.config["worker_reassignment"]["reviewer_fallbacks"],
                    "Claude": ["Qwen", "Grok", "Gemini"],
                },
            },
            "agents": {
                **self.config["agents"],
                "qwen": {"display_name": "Qwen"},
            },
        }
        worker = {
            "task_id": "RUN-001",
            "agent_id": "claude",
            "retry_count": 5,
            "run_id": "claude-run-9",
        }
        status = {
            "tasks": [
                {
                    "id": "RUN-001",
                    "status": "review_approved",
                    "owner": "Claude",
                    "reviewer": "Codex",
                }
            ]
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                config,
                worker,
                "You've hit your limit · resets 1pm (Asia/Taipei)",
                terminal=True,
            )

        self.assertEqual(reassigned_to, "Grok")
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "RUN-001")
        self.assertEqual(kwargs["new_owner"], "Grok")
        self.assertEqual(kwargs["new_reviewer"], "Codex")
        self.assertIsNone(kwargs["new_status"])


class WorkerOsDuplicateGuardTests(unittest.TestCase):
    def _make_fake_proc(self, entries: dict[int, str | None]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup_proc, root)
        for pid, cmdline in entries.items():
            pid_dir = root / str(pid)
            pid_dir.mkdir()
            if cmdline is not None:
                (pid_dir / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode("utf-8"))
        return root

    @staticmethod
    def _cleanup_proc(root: Path) -> None:
        for child in root.glob("**/*"):
            if child.is_file():
                child.unlink()
        for child in sorted(root.glob("**/*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        root.rmdir()

    def test_scan_groups_pids_by_agent_marker(self) -> None:
        proc = self._make_fake_proc(
            {
                111: "codex exec -C /tmp/wt 你的 auto worker 身分是：Codex 。 Task ID: T1",
                222: "codex exec -C /tmp/wt2 你的 auto worker 身分是：Codex2 。 Task ID: T2",
                333: "codex exec -C /tmp/wt3 你的 auto worker 身分是：Codex 。 Task ID: T3",
                444: "vim",
                555: None,
            }
        )
        result = supervisor.scan_live_worker_pids_by_agent(proc_root=proc)
        self.assertEqual(sorted(result["Codex"]), [111, 333])
        self.assertEqual(result["Codex2"], [222])
        self.assertNotIn("vim", result)

    def test_scan_skips_self_pid(self) -> None:
        proc = self._make_fake_proc(
            {os.getpid(): "auto worker 身分是：Codex"}
        )
        self.assertEqual(supervisor.scan_live_worker_pids_by_agent(proc_root=proc), {})

    def test_block_reason_flags_live_duplicate(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state: dict = {}
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42, 99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("Codex", reason)
        self.assertIn("42", reason)
        self.assertIn("99", reason)

    def test_block_reason_passes_when_guard_disabled(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": False},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Codex": [42]},
            ) as scan,
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_rejects_invalid_codex_service_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "priority"\n', encoding="utf-8")
            config = {
                "agents": {"codex": {"provider": "codex"}},
                "providers": {
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex", provider_report)

        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("unsupported service_tier", reason)
        self.assertIn("priority", reason)

    def test_block_reason_uses_hyphenated_provider_key_for_codex_slot_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "fast"\n', encoding="utf-8")
            config = {
                "agents": {
                    "codex1_1": {
                        "id": "codex1_1",
                        "provider": "codex1-1",
                        "display_name": "Codex",
                        "dispatch_slot_for": "codex",
                    }
                },
                "providers": {
                    "codex1-1": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                },
                "ready_dispatcher": {"worker_os_duplicate_guard": False},
            }
            provider_report = {"providers": {"codex1-1": {"local_cli_worker_supported": True, "supports_auto_approve": True}}}

            reason = supervisor.agent_auto_dispatch_block_reason(config, {}, "codex1_1", provider_report)

        self.assertIsNone(reason)

    def test_block_reason_ignores_other_agents_processes(self) -> None:
        config = {
            "agents": {"codex": {"provider": "codex"}},
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with (
            mock.patch.object(supervisor, "display_name_for", return_value="Codex"),
            mock.patch.object(supervisor, "agent_dispatch_paused", return_value=False),
            mock.patch.object(
                supervisor, "scan_live_worker_pids_by_agent",
                return_value={"Claude": [42], "Codex2": [99]},
            ),
        ):
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, {}, "codex", provider_report
            )
        self.assertIsNone(reason)

    def test_block_reason_allows_slotted_logical_agent_with_free_slot(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(
            supervisor,
            "scan_live_worker_pids_by_agent",
            return_value={"Codex": [42]},
        ) as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNone(reason)
        scan.assert_not_called()

    def test_block_reason_blocks_exact_slot_with_active_worker(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex1_1",
                    "status": "running",
                    "pid": 42,
                }
            }
        }
        provider_report = {"providers": {"codex1-1": {"auth_ready": True}, "codex1-2": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            blocked = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_1", provider_report
            )
            available = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex1_2", provider_report
            )
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertIn("codex1_1", blocked)
        self.assertIn("42", blocked)
        self.assertIsNone(available)
        scan.assert_not_called()

    def test_block_reason_blocks_slotted_logical_agent_when_all_slots_busy(self) -> None:
        config = {
            "agents": {
                "codex": {
                    "provider": "codex",
                    "display_name": "Codex",
                    "worker_slots": ["codex1_1", "codex1_2"],
                },
                "codex1_1": {
                    "id": "codex1_1",
                    "provider": "codex1-1",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
                "codex1_2": {
                    "id": "codex1_2",
                    "provider": "codex1-2",
                    "display_name": "Codex",
                    "dispatch_slot_for": "codex",
                },
            },
            "ready_dispatcher": {"worker_os_duplicate_guard": True},
        }
        state = {
            "workers": {
                "run-1": {"run_id": "run-1", "agent_id": "codex1_1", "status": "running", "pid": 42},
                "run-2": {"run_id": "run-2", "agent_id": "codex1_2", "status": "running", "pid": 99},
            }
        }
        provider_report = {"providers": {"codex": {"auth_ready": True}}}
        with mock.patch.object(supervisor, "scan_live_worker_pids_by_agent") as scan:
            reason = supervisor.agent_auto_dispatch_block_reason(
                config, state, "codex", provider_report
            )
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("all dispatch slots", reason)
        self.assertIn("codex1_1", reason)
        self.assertIn("codex1_2", reason)
        scan.assert_not_called()


class RuntimeLeaseReconciliationTests(unittest.TestCase):
    def _config(self, root: Path) -> dict:
        return {
            "paths": {
                "status_file": str(root / "ai-status.json"),
                "activity_log": str(root / "activity-log.jsonl"),
                "event_queue": str(root / "event-queue.jsonl"),
            },
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "status_field": "status",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {},
            "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex1"}},
            "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex"}},
        }

    def test_reconcile_runtime_requeues_started_event_without_active_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-001",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-lease",
                        "task_id": "OPS-LEASE-001",
                        "target_agent": "codex",
                        "target_display_name": "Codex",
                        "reason": "owned_in_progress_dispatch",
                        "message": "wake",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {
                    "events": {
                        "evt-lease": {
                            "status": "started",
                            "run_id": "codex-run-missing",
                            "lease_owner": "codex-run-missing",
                        }
                    }
                },
                "workers": {},
            }

            changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            record = state["queue"]["events"]["evt-lease"]
            self.assertEqual(record["status"], "queued")
            self.assertEqual(
                record["requeue_reason"],
                "started queue record had no active worker during supervisor boot reconciliation",
            )
            self.assertNotIn("lease_owner", record)
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["started_queue_records_requeued"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["started_queue_records_requeued"],
                1,
            )

    def test_restart_preserves_one_live_claude_worker_without_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["claude"] = {"delivery_mode": "claude_cli", "quota_group": "claude"}
            config["agents"]["claude"] = {
                "id": "claude",
                "display_name": "Claude",
                "provider": "claude",
            }
            config["ready_dispatcher"]["agent_order"] = ["claude"]
            task = {
                "id": "ODP-TASKOUTPUT-LIVE",
                "status": "in_progress",
                "owner": "Claude",
                "reviewer": "Codex",
                "depends_on": [],
            }
            (root / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-claude",
                        "task_id": task["id"],
                        "target_agent": "claude",
                        "target_display_name": "Claude",
                        "reason": "owned_in_progress_dispatch",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {
                    "events": {
                        "evt-claude": {
                            "status": "started",
                            "run_id": "claude-run-live",
                        }
                    }
                },
                "workers": {
                    "claude-run-live": {
                        "run_id": "claude-run-live",
                        "status": "running",
                        "provider": "claude",
                        "agent_id": "claude",
                        "task_id": task["id"],
                        "queue_event_id": "evt-claude",
                        "pid": 4242,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=True),
                mock.patch.object(supervisor, "queue_delivery_event") as queue_delivery_event,
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={"Claude": [4242]}),
            ):
                supervisor.reconcile_runtime_on_boot(config, state)
                dispatched = supervisor.dispatch_ready_tasks(
                    config,
                    state,
                    provider_report={"providers": {"claude": {"auth_ready": True}}},
                )

            active_for_task = [
                worker
                for worker in state["workers"].values()
                if worker.get("task_id") == task["id"] and worker.get("status") == "running"
            ]
            self.assertFalse(dispatched)
            self.assertEqual(len(active_for_task), 1)
            self.assertEqual(active_for_task[0]["run_id"], "claude-run-live")
            queue_delivery_event.assert_not_called()

    def test_reconcile_runtime_fails_running_worker_when_pid_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-002",
                                "status": "in_progress",
                                "owner": "Codex",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-002", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-dead"}}},
                "workers": {
                    "codex-run-dead": {
                        "run_id": "codex-run-dead",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-002",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_activity_log") as write_activity_log,
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-dead"]
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "failed")
            self.assertIn("process missing", worker["last_error"])
            activity_types = [call.args[1]["type"] for call in write_activity_log.call_args_list]
            self.assertEqual(activity_types, ["worker_failed", "worker_runtime_metrics"])
            metrics = state["worker_runtime_metrics"]
            self.assertEqual(metrics["totals"]["missing_process_workers_failed"], 1)
            self.assertEqual(
                metrics["last_measurements"]["boot_reconciliation"]["counts"]["missing_process_workers_failed"],
                1,
            )

    def test_reconcile_runtime_does_not_scan_successful_missing_worker_log_for_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "review",
                                "owner": "Claude",
                                "reviewer": "Codex",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-worker", "task_id": "OPS-LEASE-003", "target_agent": "codex"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "codex-review.log"
            log_path.write_text(
                "\n".join(
                    [
                        "**Blocker**",
                        '+ completed.stderr = b"Error: not authenticated, please login first"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            status_path = root / "runner-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "exit_code": 0,
                        "finished_at": "2026-06-01T13:07:54Z",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-worker": {"status": "started", "run_id": "codex-run-done"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "codex-run-done": {
                        "run_id": "codex-run-done",
                        "status": "running",
                        "provider": "codex",
                        "agent_id": "codex",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-worker",
                        "pid": 987654,
                        "log_path": str(log_path),
                        "runner_status_path": str(status_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence") as write_failure_evidence,
                mock.patch.object(supervisor, "mark_provider_dispatch_paused") as mark_provider_dispatch_paused,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["codex-run-done"]
            self.assertEqual(worker["status"], "completed")
            self.assertNotIn("last_error", worker)
            self.assertEqual(worker["runner_status"], "completed")
            self.assertEqual(worker["exit_code"], 0)
            self.assertEqual(state["queue"]["events"]["evt-worker"]["status"], "completed")
            self.assertEqual(state["provider_guardrails"]["dispatch_pauses"], {})
            write_failure_evidence.assert_not_called()
            mark_provider_dispatch_paused.assert_not_called()

    def test_reconcile_runtime_uses_log_failure_for_missing_process_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["gemini"] = {"delivery_mode": "gemini"}
            config["agents"]["gemini"] = {"id": "gemini", "display_name": "Gemini", "provider": "gemini"}
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "OPS-LEASE-003",
                                "status": "in_progress",
                                "owner": "Gemini",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps({"event_id": "evt-gemini", "task_id": "OPS-LEASE-003", "target_agent": "gemini"})
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "gemini-quota.log"
            log_path.write_text(
                "\n".join(
                    [
                        "Error when talking to Gemini API Full report available at: /tmp/gemini-client-error.json TerminalQuotaError: You have exhausted your capacity on this model.",
                        "reason: 'QUOTA_EXHAUSTED'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "queue": {"events": {"evt-gemini": {"status": "started", "run_id": "gemini-run-dead"}}},
                "provider_guardrails": {"dispatch_pauses": {}},
                "workers": {
                    "gemini-run-dead": {
                        "run_id": "gemini-run-dead",
                        "status": "running",
                        "provider": "gemini",
                        "agent_id": "gemini",
                        "task_id": "OPS-LEASE-003",
                        "queue_event_id": "evt-gemini",
                        "pid": 987654,
                        "log_path": str(log_path),
                    }
                },
            }

            with (
                mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/gemini.json"),
                mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Codex"),
            ):
                changed = supervisor.reconcile_runtime_on_boot(config, state)

            self.assertTrue(changed)
            worker = state["workers"]["gemini-run-dead"]
            self.assertEqual(worker["status"], "reassigned")
            self.assertEqual(worker["reassigned_to"], "Codex")
            self.assertEqual(state["queue"]["events"]["evt-gemini"]["status"], "completed")
            pause = state["provider_guardrails"]["dispatch_pauses"]["gemini"]
            self.assertEqual(pause["pause_kind"], "quota_terminal")
            self.assertEqual(pause["worker_run_id"], "gemini-run-dead")
            # Provider quota is environmental, not evidence that this task is in
            # a logic failure loop; provider pause carries the recovery state.
            self.assertNotIn(
                "OPS-LEASE-003:gemini",
                state["provider_guardrails"]["task_failure_streaks"],
            )
            self.assertIn("capacity", worker["last_error"].lower())

    def test_antigravity_boot_reconciliation_preserves_owner_for_claude_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._config(root)
            config["providers"]["antigravity5"] = {
                "delivery_mode": "antigravity",
                "antigravity": {
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "Claude Sonnet 4.6 (Thinking)",
                    }
                },
            }
            config["agents"]["antigravity5"] = {
                "id": "antigravity5",
                "display_name": "Antigravity5",
                "provider": "antigravity5",
            }
            (root / "ai-status.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "ODP-AGY-LIVE",
                                "status": "in_progress",
                                "owner": "Antigravity5",
                                "reviewer": "Claude",
                                "depends_on": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "event-queue.jsonl").write_text(
                json.dumps(
                    {
                        "event_id": "evt-agy",
                        "task_id": "ODP-AGY-LIVE",
                        "target_agent": "antigravity5",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            log_path = root / "agy-quota.log"
            log_path.write_text(
                "Error: Individual quota reached. Please upgrade your subscription "
                "to increase your limits. Resets in 2h21m32s.\n",
                encoding="utf-8",
            )
            worker = {
                "run_id": "agy-run-dead",
                "status": "running",
                "provider": "antigravity5",
                "agent_id": "antigravity5",
                "task_id": "ODP-AGY-LIVE",
                "queue_event_id": "evt-agy",
                "pid": 987654,
                "log_path": str(log_path),
                "antigravity_model_pool": "gemini",
                "metadata": {"antigravity_model_pool": "gemini"},
            }
            state = {
                "queue": {"events": {"evt-agy": {"status": "started", "run_id": "agy-run-dead"}}},
                "workers": {"agy-run-dead": worker},
            }

            previous_state_path = supervisor.model_rotation._STATE_PATH
            supervisor.model_rotation._STATE_PATH = root / "model-cooldown.json"
            try:
                with (
                    mock.patch.object(supervisor, "pid_is_alive", return_value=False),
                    mock.patch.object(supervisor, "write_failure_evidence", return_value="evidence/agy.json"),
                    mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure") as reassign,
                ):
                    self.assertTrue(supervisor.reconcile_runtime_on_boot(config, state))
                    self.assertFalse(supervisor.reconcile_runtime_on_boot(config, state))
                    self.assertEqual(
                        supervisor.model_rotation.resolve_active_selection(config, "antigravity5")["pool"],
                        "claude",
                    )
            finally:
                supervisor.model_rotation._STATE_PATH = previous_state_path

            reassign.assert_not_called()
            self.assertEqual(worker["status"], "failed")
            self.assertEqual(state["queue"]["events"]["evt-agy"]["status"], "failed")
            processed = state["provider_guardrails"]["processed_model_rotation_failures"]
            self.assertEqual(list(processed), ["agy-run-dead"])

    def test_quota_group_cap_blocks_second_slot(self) -> None:
        config = {
            "ready_dispatcher": {"max_concurrent_per_quota_group": {"codex1": 1}},
            "agents": {
                "codex1_1": {"id": "codex1_1", "display_name": "Codex", "provider": "codex1-1"},
                "codex1_2": {"id": "codex1_2", "display_name": "Codex", "provider": "codex1-2"},
            },
            "providers": {
                "codex1-1": {"quota_group": "codex1"},
                "codex1-2": {"quota_group": "codex1"},
            },
        }
        state = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "status": "running",
                    "agent_id": "codex1_1",
                    "provider": "codex1-1",
                    "quota_group": "codex1",
                }
            }
        }

        reason = supervisor.agent_auto_dispatch_block_reason(config, state, "codex1_2", provider_report={})

        self.assertIsNotNone(reason)
        self.assertIn("quota group codex1", reason or "")


class MaxConcurrentWorkersCapTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "ready_dispatcher": {
                "max_concurrent_workers": 2,
                "max_dispatches_per_tick": 4,
                "enabled": True,
            },
            "schema": {},
            "agents": {},
        }

    def test_dispatch_ready_tasks_skips_when_global_cap_reached(self) -> None:
        config = self._base_config()
        state: dict = {}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [1, 2], "Claude": [3]},
            ) as scan,
            mock.patch.object(supervisor, "weighted_dispatch_agent_ids", return_value=["codex"]),
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "agent_dispatch_loads", return_value={}),
            mock.patch.object(supervisor, "failure_loop_agents_for_task_map", return_value=set()),
            mock.patch.object(supervisor, "chair_rotation_state", return_value={}),
            mock.patch.object(supervisor, "helper_claim_settings", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(supervisor, "start_worker_for_request") as start,
        ):
            changed = supervisor.dispatch_ready_tasks(config, state)
        scan.assert_called()
        start.assert_not_called()
        self.assertFalse(changed)

    def test_dispatch_ready_tasks_proceeds_when_under_cap(self) -> None:
        config = self._base_config()
        state: dict = {}
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": []}),
            mock.patch.object(
                supervisor,
                "scan_live_worker_pids_by_agent",
                return_value={"Codex": [1]},
            ),
            mock.patch.object(supervisor, "weighted_dispatch_agent_ids", return_value=["codex"]),
            mock.patch.object(supervisor, "active_worker_indexes", return_value=(set(), set())),
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "agent_dispatch_loads", return_value={}),
            mock.patch.object(supervisor, "failure_loop_agents_for_task_map", return_value=set()),
            mock.patch.object(supervisor, "chair_rotation_state", return_value={}),
            mock.patch.object(supervisor, "helper_claim_settings", return_value={}),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value="blocked"),
            mock.patch.object(supervisor, "start_worker_for_request") as start,
        ):
            supervisor.dispatch_ready_tasks(config, state)
        start.assert_not_called()


class PruneOrphanWorktreesTests(unittest.TestCase):
    def _stub_subprocess_run(self, results):
        def fake_run(cmd, *args, **kwargs):
            cmd_tuple = tuple(str(c) for c in cmd)
            for key, value in results.items():
                if cmd_tuple[: len(key)] == key:
                    return value
            raise AssertionError(f"unexpected subprocess.run call: {cmd_tuple}")
        return fake_run

    def test_returns_false_when_disabled(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": False}}
        state: dict = {}
        self.assertFalse(supervisor.prune_orphan_worktrees(config, state))

    def test_throttled_within_interval(self) -> None:
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        recent_ts = (_dt.now(UTC) - _td(seconds=30)).isoformat().replace("+00:00", "Z")
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 600}}
        state = {"worker_worktree_housekeeping": {"last_run_at": recent_ts}}
        with mock.patch.object(supervisor, "worker_worktree_settings") as ws:
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)
        ws.assert_not_called()

    def test_skips_when_no_merged_branches(self) -> None:
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True, "root": "/tmp/wt"}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=Path("/tmp/wt")),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", return_value=False),
            mock.patch.object(Path, "exists", return_value=True),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def test_removes_clean_merged_orphan(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [
            {"worktree": record_path, "branch": "refs/heads/task/X"},
            {"worktree": "/repo", "branch": "refs/heads/main"},
        ]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="  task/X\n", stderr="")
        clean_status = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        remove_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): clean_status,
            ("git", "-C", "/repo", "worktree", "remove", record_path): remove_ok,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertTrue(result)

    def test_skips_dirty_worktree(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M foo.py\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
            ("git", "-C", record_path, "status", "--porcelain"): dirty_status,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state: dict = {}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)

    def test_skips_worktree_claimed_by_active_worker(self) -> None:
        base = Path("/tmp/wt").resolve()
        record_path = str(base / "task-x")
        records = [{"worktree": record_path, "branch": "refs/heads/task/X"}]
        merged_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="task/X\n", stderr="")
        runs = {
            ("git", "branch", "--merged"): merged_proc,
        }
        config = {"worker_worktree_housekeeping": {"enabled": True, "tick_interval_seconds": 0}}
        state = {"workers": {"r-1": {"workspace_path": record_path}}}
        with (
            mock.patch.object(supervisor, "worker_worktree_settings", return_value={"enabled": True}),
            mock.patch.object(supervisor, "_worker_worktree_base_root", return_value=base),
            mock.patch.object(supervisor, "config_path", return_value=Path("/repo/ai-status.json")),
            mock.patch.object(supervisor, "_scan_process_paths_in_root", return_value=set()),
            mock.patch.object(supervisor, "_git_ref_exists", side_effect=lambda _root, ref: ref == "origin/dev"),
            mock.patch.object(supervisor, "_git_worktree_records", return_value=records),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(supervisor.subprocess, "run", side_effect=self._stub_subprocess_run(runs)),
        ):
            result = supervisor.prune_orphan_worktrees(config, state)
        self.assertFalse(result)


class ResolvePollIntervalTests(unittest.TestCase):
    def test_default_uses_config_value(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, 300.0)
        self.assertEqual(source, "config")

    def test_cli_value_at_or_above_config_does_not_require_authorization(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=600.0, allow_fast_poll=False
        )
        self.assertEqual(value, 600.0)
        self.assertEqual(source, "cli")

    def test_cli_value_below_config_requires_allow_fast_poll(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit) as ctx:
            supervisor.resolve_poll_interval(
                config, cli_value=60.0, allow_fast_poll=False
            )
        self.assertIn("--allow-fast-poll", str(ctx.exception))

    def test_cli_value_below_config_allowed_when_authorized(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        value, source = supervisor.resolve_poll_interval(
            config, cli_value=60.0, allow_fast_poll=True
        )
        self.assertEqual(value, 60.0)
        self.assertEqual(source, "cli")

    def test_zero_or_negative_cli_value_rejected(self) -> None:
        config = {"supervisor": {"poll_interval_seconds": 300}}
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=0.0, allow_fast_poll=True
            )
        with self.assertRaises(SystemExit):
            supervisor.resolve_poll_interval(
                config, cli_value=-5.0, allow_fast_poll=True
            )

    def test_missing_config_falls_back_to_default(self) -> None:
        value, source = supervisor.resolve_poll_interval(
            {}, cli_value=None, allow_fast_poll=False
        )
        self.assertEqual(value, supervisor.CONFIG_DEFAULT_POLL_INTERVAL_SECONDS)
        self.assertEqual(source, "config")


class RunSupervisorShellGuardTests(unittest.TestCase):
    def _script(self) -> Path:
        return Path(supervisor.__file__).resolve().parent.parent / "scripts" / "run-supervisor.sh"

    def _run(self, args: list[str], stub_body: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "python3"
            stub.write_text(stub_body)
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            return subprocess.run(
                ["bash", str(self._script()), *args],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_poll_interval_without_allow_fast_poll_is_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval", "60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_equals_form_also_rejected(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--poll-interval=60"], "#!/bin/sh\necho 'should not run' >&2\nexit 99\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--allow-fast-poll", proc.stderr)

    def test_poll_interval_with_allow_fast_poll_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(
            ["--poll-interval", "60", "--allow-fast-poll"], '#!/bin/sh\nexit 7\n'
        )
        self.assertEqual(proc.returncode, 7, proc.stderr)

    def test_no_poll_interval_passes_through(self) -> None:
        script = self._script()
        if not script.exists():
            self.skipTest("run-supervisor.sh not present")
        proc = self._run(["--verbose"], '#!/bin/sh\nexit 11\n')
        self.assertEqual(proc.returncode, 11, proc.stderr)



class ReviewHeadFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.test_dir.name)
        ai_status.STATUS_FILE = self.tmp_path / "ai-status.json"
        ai_status.LOG_FILE = self.tmp_path / "ai-activity-log.jsonl"
        ai_status.CURRENT_WORK_FILE = self.tmp_path / "current-work.md"
        ai_status.STATUS_FILE.write_text("{}", encoding="utf-8")
        ai_status.LOG_FILE.write_text("", encoding="utf-8")
        ai_status.clear_ai_status_caches()

    def tearDown(self) -> None:
        ai_status.clear_ai_status_caches()
        self.test_dir.cleanup()

    def _build_freeze_test_config(self) -> dict[str, Any]:
        config = load_test_config()
        ready_disp = config.setdefault("ready_dispatcher", {})
        ready_disp["enabled"] = True
        ready_disp["review_statuses"] = ["review"]
        ready_disp["finalize_statuses"] = ["review_approved"]
        ready_disp["owned_statuses"] = ["in_progress", "todo"]
        ready_disp["active_worker_statuses"] = ["running", "waiting_approval"]
        max_by_agent = ready_disp.setdefault("max_tasks_per_agent_by_agent", {})
        max_by_agent["Antigravity4"] = 10
        max_by_agent["antigravity4"] = 10
        quota_groups = ready_disp.setdefault("max_concurrent_per_quota_group", {})
        quota_groups["antigravity4"] = 10
        quota_groups["antigravity"] = 10
        agents = config.setdefault("agents", {})
        agents["antigravity4"] = {
            "display_name": "Antigravity4",
            "provider": "antigravity",
            "adapter": "antigravity",
        }
        return config

    def test_approve_saves_approved_head_and_rejects_same_owner_reviewer(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-001",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                },
                {
                    "id": "FREEZE-TEST-002",
                    "owner": "Claude",
                    "reviewer": "Claude",
                    "status": "review",
                },
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"):
            with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"):
                with unittest.mock.patch("ai_status.sync_all"):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_approve(state, ["FREEZE-TEST-002", "Approve self"])
                    self.assertIn("must be separate identities", str(cm.exception))

                    ai_status.command_approve(state, ["FREEZE-TEST-001", "Approve valid"])
                    task = ai_status.get_task(state, "FREEZE-TEST-001")
                    self.assertEqual(task["status"], "review_approved")
                    self.assertEqual(task["approved_head"], "1111111122222222333333334444444455555555")

    def test_command_done_rejects_mutated_head(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-003",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                return_value={
                    "verified_head": "9999999922222222333333334444444455555555",
                    "pull_request": {
                        "head_sha": "1111111122222222333333334444444455555555",
                        "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                    },
                },
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003", "Finalize done"])
                self.assertIn("differs from reviewer-approved head", str(cm.exception))

    def test_command_done_fails_closed_when_delivery_checkout_sha_unresolved_or_collector_raises(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-003B",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                return_value={
                    "verified_head": None,
                    "pull_request": {
                        "head_sha": "1111111122222222333333334444444455555555",
                        "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                    },
                },
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003B", "Finalize done"])
                self.assertIn("differs from reviewer-approved head", str(cm.exception))

            with unittest.mock.patch(
                "ai_status.collect_done_delivery_metadata",
                side_effect=SystemExit("Cannot finalize task: task-owned checkout HEAD is unavailable."),
            ):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_done(state, ["FREEZE-TEST-003B", "Finalize done"])
                self.assertIn("task-owned checkout HEAD is unavailable", str(cm.exception))

    def test_supervisor_reverts_mutated_approved_head_to_review_on_disk(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            status_file = tmppath / "ai-status.json"
            initial_status = {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-004",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review_approved",
                        "approved_head": "1111111122222222333333334444444455555555",
                    }
                ]
            }
            status_file.write_text(json.dumps(initial_status), encoding="utf-8")

            config = self._build_freeze_test_config()
            config["paths"]["status_file"] = str(status_file)
            state = {
                "seen_event_keys": {},
                "ready_dispatcher": {"weighted_cursor": 0},
                "status": initial_status,
            }

            ai_status.clear_ai_status_caches()
            with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
                 unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value="8888888822222222333333334444444455555555"), \
                 unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
                 unittest.mock.patch("supervisor.sync_status_pipeline", return_value=True), \
                 unittest.mock.patch("supervisor.write_activity_log"):
                supervisor.dispatch_ready_tasks(
                    config,
                    state,
                    agent_ids_override=["antigravity4"],
                )
                disk_status = json.loads(status_file.read_text(encoding="utf-8"))
                disk_task = disk_status["tasks"][0]
                self.assertEqual(disk_task["status"], "review")
                self.assertIn("re-review required", disk_task["next"])
                self.assertNotIn("approved_head", disk_task)

    def test_task_pr_ci_status_handles_checkrun_completed_failure(self) -> None:
        ai_status.clear_ai_status_caches()
        fake_payload = {
            "state": "OPEN",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "build",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                }
            ],
        }
        with unittest.mock.patch("ai_status.run_gh_json_command", return_value=fake_payload):
            pr_state, ci_status = ai_status.task_pr_ci_status("TEST-CI-001")
            self.assertEqual(pr_state, "OPEN")
            self.assertEqual(ci_status, "failure")

    def test_dispatch_priority_for_task_and_agent_primary_work(self) -> None:
        config = self._build_freeze_test_config()
        task_mutated = {
            "id": "FREEZE-TEST-007",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        status_data = {"tasks": [task_mutated]}
        task_map = {"FREEZE-TEST-007": task_mutated}

        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"):
            prio = supervisor.dispatch_priority_for_task(config, task_mutated, "Antigravity4", task_map=task_map)
            self.assertIsNone(prio)
            has_work = supervisor.agent_has_dispatchable_primary_work(config, status_data, "Antigravity4", task_map)
            self.assertFalse(has_work)

    def test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci(self) -> None:
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-007B",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        task_map = {"FREEZE-TEST-007B": task}

        ai_status.clear_ai_status_caches()
        # Positive control: matching head + green CI MUST yield the finalize priority.
        # Without this, a config/schema regression would make every negative
        # sub-case below pass vacuously (the B13 failure mode).
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertEqual(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map),
                1,
            )

        # Head sub-cases pin CI to a *passing* probe so the head gate is the only
        # thing that can produce None. Leaving task_pr_ci_status unpatched here
        # lets control fall through to the CI gate, where the real (unmocked)
        # probe shells out to `gh`, returns ("unknown"), and produces the None
        # the assertion is checking -- which is how B14/B17/B18 stayed vacuous.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", side_effect=RuntimeError("git error")), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        # Head drifted off the approved head, CI green: still must not dispatch.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "unknown")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", side_effect=RuntimeError("gh error")):
            self.assertIsNone(supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map))

    def test_approve_fails_closed_when_approved_head_cannot_be_resolved(self) -> None:
        """B20: approving without freezing a head silently disables the freeze.

        command_done and both supervisor dispatch gates are guarded by
        `if approved_head:`. Recording no head therefore does not fail closed --
        it opts the task out of the integrity check entirely. Approval must
        abort instead, leaving the task in `review`.
        """
        def _fresh_state() -> dict[str, Any]:
            return {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-020A",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review",
                    }
                ]
            }

        # Positive control: a resolvable head still approves and freezes.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.append_log"), \
             unittest.mock.patch("ai_status.sync_all"):
            ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], "1111111122222222333333334444444455555555")

        # Unresolvable head -> abort, and leave no half-applied approval behind.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        self.assertIn("could not be resolved", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("approved_head", task)

        # A raising probe must fail closed too, not escape as a traceback.
        state = _fresh_state()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", side_effect=RuntimeError("gh down")), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020A", "Approve valid"])
        self.assertIn("Integrity gate failed closed", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020A")
        self.assertEqual(task["status"], "review")
        self.assertNotIn("approved_head", task)

    def test_approve_refuses_to_overwrite_uncleared_approved_head(self) -> None:
        """B20: approved_head is immutable for the lifetime of one approval.

        Every transition back to `review` pops approved_head, so a task in
        `review` still carrying one is inconsistent state. Silently overwriting
        it would re-freeze on a head the reviewer never signed off.
        """
        old_head = "1111111122222222333333334444444455555555"
        new_head = "9999999922222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-020B",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                    "approved_head": old_head,
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=new_head), \
             unittest.mock.patch("ai_status.sync_all"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_approve(state, ["FREEZE-TEST-020B", "Approve drifted"])
        self.assertIn("uncleared approved head", str(cm.exception))
        task = ai_status.get_task(state, "FREEZE-TEST-020B")
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["approved_head"], old_head)

        # Positive control: re-approving at the *same* head is not a conflict,
        # so the guard cannot be satisfied by rejecting every stale-head task.
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=old_head), \
             unittest.mock.patch("ai_status.append_log"), \
             unittest.mock.patch("ai_status.sync_all"):
            ai_status.command_approve(state, ["FREEZE-TEST-020B", "Approve same head"])
        task = ai_status.get_task(state, "FREEZE-TEST-020B")
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], old_head)

    def _run_finalize_dispatch_capturing_signals(
        self,
        config: dict[str, Any],
        task: dict[str, Any],
        *,
        head: Any,
        ci: Any,
    ) -> tuple[bool, list[dict[str, Any]], unittest.mock.MagicMock]:
        """Drive dispatch_ready_tasks once and collect the operator signals."""
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"weighted_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}
        logged: list[dict[str, Any]] = []

        ai_status.clear_ai_status_caches()
        head_patch = (
            unittest.mock.patch("ai_status.resolve_task_sha", side_effect=head)
            if isinstance(head, Exception)
            else unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
        )
        with head_patch, \
             unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", ci)), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.sync_status_pipeline"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.write_activity_log", side_effect=lambda _c, e: logged.append(e)), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
        return dispatched, logged, mock_queue

    def test_supervisor_emits_operator_signal_for_silent_finalize_suppression(self) -> None:
        """B20: suppressing finalize dispatch must not be silent.

        The unresolved-head path and the catch-all unresolved-CI path both used
        a bare `continue`, unlike the pending/failure branches, so a task could
        sit in review_approved indefinitely with no `next` and no activity-log
        entry explaining why nothing was happening.
        """
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()

        def _task() -> dict[str, Any]:
            return {
                "id": "FREEZE-TEST-020C",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
                "approved_head": approved,
            }

        # Positive control: head matches and CI is green -> dispatch, no signal.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()
        self.assertEqual([], [e["type"] for e in logged])

        # Head unresolvable: suppressed, task stays review_approved, signal emitted.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=None, ci="success"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("approved_head_unresolved", [e["type"] for e in logged])
        self.assertIn("Cannot verify branch HEAD", task["next"])

        # Head resolution raising is the same suppression path.
        task = _task()
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=RuntimeError("git down"), ci="success"
        )
        self.assertFalse(dispatched)
        self.assertIn("approved_head_unresolved", [e["type"] for e in logged])

        # CI probe inconclusive: suppressed with its own distinct signal.
        task = _task()
        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="unknown"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("ci_status_unresolved", [e["type"] for e in logged])
        self.assertIn("is unresolved (unknown)", task["next"])

        # Signals are emitted once, not re-logged every supervisor cycle.
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="unknown"
        )
        self.assertFalse(dispatched)
        self.assertEqual([], [e["type"] for e in logged])

    def test_ci_pending_escalates_to_operator_after_timeout(self) -> None:
        """AC3 escalation half: covered by nothing until this test.

        Mutating the whole `if ci_status == "pending":` branch to `if False:`
        left the suite green, because the later catch-all still suppressed
        dispatch. Only the ci_pending_since_ts bookkeeping and the 30-minute
        escalation notice were lost -- and nothing asserted on either.
        """
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()

        def _task(**extra: Any) -> dict[str, Any]:
            task = {
                "id": "FREEZE-TEST-020D",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
                "approved_head": approved,
            }
            task.update(extra)
            return task

        # First pending cycle: start the clock, do not escalate yet.
        task = _task()
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertFalse(dispatched)
        self.assertIn("ci_pending_since_ts", task)
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])

        # Still inside the 30-minute window: still no escalation.
        task = _task(ci_pending_since_ts=datetime.now(UTC).timestamp() - 60)
        _, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])

        # Past 1800s: escalate to the operator exactly once.
        task = _task(ci_pending_since_ts=datetime.now(UTC).timestamp() - 2000)
        _, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertIn("ci_pending_timeout", [e["type"] for e in logged])
        self.assertIn("pending for over 30 minutes", task["next"])

        _, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="pending"
        )
        self.assertEqual([], [e["type"] for e in logged])

        # Recovery: a green probe clears the pending bookkeeping and dispatches.
        dispatched, _, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()
        self.assertNotIn("ci_pending_since_ts", task)

    def test_explicit_re_review_command(self) -> None:
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-008",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": "1111111122222222333333334444444455555555",
                }
            ]
        }
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.load_state", return_value=state), \
             unittest.mock.patch("ai_status.save_state"), \
             unittest.mock.patch("ai_status.sync_all"), \
             unittest.mock.patch("ai_status.emit_status_checks_for_changed_tasks"):
            ai_status.main(["ai_status.py", "re_review", "FREEZE-TEST-008", "Updated branch with strict merge"])
            task = ai_status.get_task(state, "FREEZE-TEST-008")
            self.assertEqual(task["status"], "review")
            self.assertNotIn("approved_head", task)
            self.assertEqual(task["next"], "Updated branch with strict merge")

            task["status"] = "review_approved"
            task["approved_head"] = "1111111122222222333333334444444455555555"
            ai_status.main(["ai_status.py", "re-review", "FREEZE-TEST-008", "Alias re-review check"])
            self.assertEqual(task["status"], "review")
            self.assertNotIn("approved_head", task)

    def test_higher_priority_ready_task_exists_refuses_undispatchable_finalize_task(self) -> None:
        """B24: higher_priority_ready_task_exists must fail closed on undispatchable finalize tasks.

        A review_approved task with missing approved_head, head mismatch, or pending CI
        cannot be dispatched by dispatch_ready_tasks. It MUST NOT cause
        higher_priority_ready_task_exists to return True and terminate running workers.
        """
        approved_head = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()
        config["ready_dispatcher"]["max_tasks_per_agent_by_agent"]["Antigravity4"] = 1
        config["ready_dispatcher"]["max_concurrent_per_quota_group"]["antigravity4"] = 1

        worker = {
            "run_id": "run-001",
            "task_id": "INPROG-001",
            "agent_id": "antigravity4",
            "status": "running",
            "request_snapshot": {"reason": supervisor.REASON_OWNED_IN_PROGRESS},
        }

        task_map = {
            "INPROG-001": {
                "id": "INPROG-001",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "in_progress",
            },
            "FINAL-001": {
                "id": "FINAL-001",
                "owner": "Antigravity4",
                "reviewer": "Claude",
                "status": "review_approved",
            },
        }

        # No approved_head -> must return False (does not preempt)
        self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # Head mismatch -> must return False
        task_map["FINAL-001"]["approved_head"] = approved_head
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="9999999922222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # CI pending -> must return False
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved_head), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "pending")):
            self.assertFalse(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

        # Positive control: matching head + green CI -> returns True (preempts)
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved_head), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_dispatch_capacity", return_value=1):
            self.assertTrue(supervisor.higher_priority_ready_task_exists(config, worker, task_map))

    def test_supervisor_suppresses_finalize_dispatch_on_pending_ci(self) -> None:
        config = self._build_freeze_test_config()
        task_worktree_status_path = ROOT_DIR / "ai-status.json"
        task_worktree_status_before = (
            task_worktree_status_path.read_bytes()
            if task_worktree_status_path.exists()
            else None
        )
        task = {
            "id": "FREEZE-TEST-005",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"weighted_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}

        ai_status.clear_ai_status_caches()
        # Positive Control: ci_status = "success" MUST dispatch
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Test suppression: ci_status = "pending" MUST NOT dispatch
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555"), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "pending")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")
        task_worktree_status_after = (
            task_worktree_status_path.read_bytes()
            if task_worktree_status_path.exists()
            else None
        )
        self.assertEqual(task_worktree_status_before, task_worktree_status_after)

    def test_supervisor_suppresses_finalize_dispatch_on_unresolved_head_or_unknown_ci(self) -> None:
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-005B",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        state = {
            "seen_event_keys": {},
            "ready_dispatcher": {"weighted_cursor": 0},
            "status": {"tasks": [task]},
        }
        status = {"tasks": [task]}
        APPROVED = "1111111122222222333333334444444455555555"

        ai_status.clear_ai_status_caches()

        # Sub-case 1: resolve_task_sha returns None — head cannot be resolved, must suppress.
        # Positive control: matching head + ci=success MUST dispatch.
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: resolve_task_sha returns None — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=None), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

        # Sub-case 2: ci_status = "unknown" — must suppress.
        # Positive control: matching head + ci=success MUST dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: ci_status = "unknown" — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "unknown")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

        # Sub-case 3: task_pr_ci_status raises — must suppress (fail closed on error).
        # Positive control: matching head + ci=success MUST dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertTrue(dispatched)
            mock_queue.assert_called_once()

        # Negative: task_pr_ci_status raises RuntimeError — must NOT dispatch.
        state["seen_event_keys"] = {}
        task["status"] = "review_approved"
        with unittest.mock.patch("supervisor.scan_live_worker_pids_by_agent", return_value={}), \
             unittest.mock.patch("supervisor.outstanding_delivery_indexes", return_value=(set(), set(), set())), \
             unittest.mock.patch("supervisor.agent_dispatch_loads", return_value={}), \
             unittest.mock.patch("supervisor.load_status", return_value=status), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", side_effect=RuntimeError("gh error")), \
             unittest.mock.patch("supervisor.agent_auto_dispatch_block_reason", return_value=None), \
             unittest.mock.patch("supervisor.write_activity_log"), \
             unittest.mock.patch("supervisor.write_json"), \
             unittest.mock.patch("supervisor.queue_delivery_event", return_value=True) as mock_queue:
            dispatched = supervisor.dispatch_ready_tasks(
                config,
                state,
                agent_ids_override=["antigravity4"],
            )
            self.assertFalse(dispatched)
            mock_queue.assert_not_called()
            self.assertEqual(task["status"], "review_approved")

    def test_supervisor_finalize_dispatch_forces_fresh_sha_resolution(self) -> None:
        """Verify supervisor evaluate_dispatch and finalize dispatch gate pass force_refresh=True to resolve_task_sha."""
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-FRESH-001",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="1111111122222222333333334444444455555555") as mock_resolve, \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            prio = supervisor.dispatch_priority_for_task(config, task, "Antigravity4")
            self.assertIsNotNone(prio)
            mock_resolve.assert_called_with("FREEZE-TEST-FRESH-001", force_refresh=True)

    def test_task_review_gate_status_check_pending_on_head_mismatch(self) -> None:
        task = {
            "id": "FREEZE-TEST-006",
            "reviewer": "Claude",
            "approved_head": "1111111122222222333333334444444455555555",
        }
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value="7777777722222222333333334444444455555555"):
            with unittest.mock.patch("ai_status.get_repository_slug_safe", return_value="alfloop-dev/odayplus"):
                with unittest.mock.patch("subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ai_status.emit_task_review_status_check(task, "review_approved")
                    mock_run.assert_called_once()
                    cmd = mock_run.call_args[0][0]
                    self.assertIn("state=pending", cmd)
                    self.assertIn("re-review required", "".join(cmd))

    # ------------------------------------------------------------------
    # B22 -- `review_approved` with the approved_head key *absent*.
    #
    # Round 8 rejected the freeze because every consumer was written as
    # `if approved_head:`, so the missing-key shape opted a task out of the
    # control entirely. Mutation testing could not find it: a mutant only dies
    # inside a path some test reaches, and all 32 approved_head references in
    # the suite *set* the key. These tests construct the shape directly.
    # ------------------------------------------------------------------

    MISSING_HEAD_APPROVED = "3333333322222222333333334444444455555555"

    def _missing_head_task(self, task_id: str) -> dict[str, Any]:
        """The live ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 shape, verbatim:
        review_approved, owned, and simply carrying no `approved_head` key."""
        return {
            "id": task_id,
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
        }

    def test_dispatch_priority_fails_closed_when_approved_head_is_absent(self) -> None:
        config = self._build_freeze_test_config()
        task = self._missing_head_task("FREEZE-TEST-022A")
        task_map = {task["id"]: task}
        ai_status.clear_ai_status_caches()

        # The gate must not consult the head/CI probes at all -- there is
        # nothing to compare against -- so both are pinned green. If the
        # suppression came from the CI probe instead, this would pass vacuously.
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
            self.assertIsNone(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map=task_map)
            )

            # Control: the identical task with the key present and matching is
            # dispatched, so the assertion above is the gate firing, not a
            # broken fixture.
            control = dict(task, approved_head=self.MISSING_HEAD_APPROVED)
            self.assertEqual(
                supervisor.dispatch_priority_for_task(
                    config, control, "Antigravity4", task_map={control["id"]: control}
                ),
                1,
            )

    def test_dispatch_ready_suppresses_and_signals_when_approved_head_is_absent(self) -> None:
        config = self._build_freeze_test_config()
        task = self._missing_head_task("FREEZE-TEST-022B")

        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertEqual(task["status"], "review_approved")
        self.assertIn("approved_head_missing", [e["type"] for e in logged])
        self.assertIn("no reviewer-approved head", task["next"])
        self.assertIn("restore_approved_head", task["next"])

        # Emitted once, not every supervisor cycle.
        dispatched, logged, _ = self._run_finalize_dispatch_capturing_signals(
            config, task, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertFalse(dispatched)
        self.assertEqual([], [e["type"] for e in logged])

        # Control: the same task with the head recorded does dispatch.
        control = self._missing_head_task("FREEZE-TEST-022B")
        control["approved_head"] = self.MISSING_HEAD_APPROVED
        dispatched, _, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, control, head=self.MISSING_HEAD_APPROVED, ci="success"
        )
        self.assertTrue(dispatched)
        mock_queue.assert_called_once()

    def test_command_done_rejects_absent_head_before_delivery_metadata(self) -> None:
        """The freeze gate must reject, not merely be followed by gates that do.

        Round 8's reproduction reached the commit-convention and require_merged_pr
        messages, which proved the freeze had never fired. Those gates check
        merge hygiene, not "is this the commit a reviewer read", so this asserts
        collect_done_delivery_metadata is never entered.
        """
        state = {"tasks": [self._missing_head_task("FREEZE-TEST-022C")]}
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.collect_done_delivery_metadata") as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-022C", "Finalize"])
        self.assertIn("no reviewer-approved head", str(cm.exception))
        collect.assert_not_called()
        self.assertEqual(ai_status.get_task(state, "FREEZE-TEST-022C")["status"], "review_approved")

    def test_task_review_gate_pending_when_approved_head_is_absent(self) -> None:
        task = {"id": "FREEZE-TEST-022D", "reviewer": "Claude"}
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.resolve_task_sha", return_value=self.MISSING_HEAD_APPROVED), \
             unittest.mock.patch("ai_status.get_repository_slug_safe", return_value="alfloop-dev/odayplus"), \
             unittest.mock.patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            ai_status.emit_task_review_status_check(task, "review_approved")
            cmd = mock_run.call_args[0][0]
            self.assertIn("state=pending", cmd)
            self.assertIn("no reviewer-approved head is recorded", "".join(cmd))

    def test_staging_shape_closes_only_after_explicit_head_restoration(self) -> None:
        """End-to-end on the exact live shape: head e0147acb, merge 4b329493.

        Also pins B22-3: the freeze anchors on the immutable PR head, and the
        merge commit GitHub created is recorded as separate delivery evidence
        rather than being accepted as the reviewed commit.
        """
        head = "e0147acb22222222333333334444444455555555"
        merge = "4b32949322222222333333334444444455555555"
        task_id = "ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001"
        state = {"tasks": [self._missing_head_task(task_id)]}
        task = ai_status.get_task(state, task_id)
        config = self._build_freeze_test_config()
        ai_status.clear_ai_status_caches()

        resolve = unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
        pr_info = unittest.mock.patch(
            "ai_status.task_pr_head_and_merge_commit", return_value=(head, merge)
        )

        # 1. Nothing dispatches and nothing finalizes while the head is missing.
        with resolve, pr_info, unittest.mock.patch("ai_status.append_log"):
            self.assertIsNone(
                supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map={task_id: task})
            )
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch("ai_status.collect_done_delivery_metadata") as collect:
                with self.assertRaises(SystemExit):
                    ai_status.command_done(state, [task_id, "Finalize"])
                collect.assert_not_called()

            # 2. The owner cannot attest their own head.
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, head, "self-attest"])
                self.assertIn("Only the reviewer", str(cm.exception))
            self.assertNotIn("approved_head", task)

            # 3. The reviewer cannot attest the merge commit, an abbreviation,
            #    or any sha other than the immutable PR head.
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, merge, "merge commit"])
                self.assertIn("merge commit", str(cm.exception))

                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(state, [task_id, head[:8], "abbrev"])
                self.assertIn("40-character commit sha", str(cm.exception))

                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved_head(
                        state, [task_id, "9999999922222222333333334444444455555555", "other head"]
                    )
                self.assertIn("does not match the task branch head", str(cm.exception))
                self.assertNotIn("approved_head", task)

                # 4. Attesting the exact reviewed head restores the freeze.
                ai_status.command_restore_approved_head(state, [task_id, head, "attest reviewed head"])
            self.assertEqual(task["approved_head"], head)

            # 5. Only now does the task dispatch and finalize, and the merge
            #    commit is recorded as a distinct fact from the approved head.
            with unittest.mock.patch("ai_status.task_pr_ci_status", return_value=("OPEN", "success")):
                self.assertEqual(
                    supervisor.dispatch_priority_for_task(config, task, "Antigravity4", task_map={task_id: task}),
                    1,
                )
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch(
                     "ai_status.collect_done_delivery_metadata",
                     return_value={
                         "verified_head": head,
                         "pull_request": {"head_sha": head, "merge_commit": merge},
                     },
                 ), \
                 unittest.mock.patch("ai_status.archive_terminal_task_from_state"):
                ai_status.command_done(state, [task_id, "Finalize"])

        self.assertEqual(task["status"], "done")
        self.assertEqual(task["delivery"]["approved_head"], head)
        self.assertEqual(task["delivery"]["verified_head"], head)
        self.assertEqual(task["delivery"]["pr_merge_commit"], merge)
        self.assertNotEqual(task["delivery"]["pr_merge_commit"], task["delivery"]["approved_head"])

    def test_done_refuses_when_resolved_head_is_the_merge_commit(self) -> None:
        """If a task branch resolves to the merge commit, the frozen head must
        not be satisfiable by it -- the merge commit was never reviewed."""
        merge = "4b32949322222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-022E",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review_approved",
                    "approved_head": merge,
                }
            ]
        }
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch(
                 "ai_status.collect_done_delivery_metadata",
                 return_value={
                     "verified_head": merge,
                     "pull_request": {
                         "head_sha": "e0147acb22222222333333334444444455555555",
                         "merge_commit": merge,
                     },
                 },
             ) as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-022E", "Finalize"])
        self.assertIn("differs from reviewer-approved head", str(cm.exception))
        collect.assert_called_once()

    # ------------------------------------------------------------------
    # B21 -- `restore_approved` is the second producer of `review_approved`.
    # ------------------------------------------------------------------

    def _restore_state(self, **task_extra: Any) -> dict[str, Any]:
        task = {
            "id": "FREEZE-TEST-021",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "in_progress",
            "review_notes_zh": "已審核通過",
        }
        task.update(task_extra)
        return {"tasks": [task]}

    def test_restore_approved_refuses_without_a_durable_approved_head(self) -> None:
        """The round-8 reproduction: approve -> reopen -> restore -> done at an
        unreviewed head. `reopen` pops approved_head but keeps review_notes_zh,
        which is exactly restore_approved's precondition."""
        approved = "1111111122222222333333334444444455555555"
        state = {
            "tasks": [
                {
                    "id": "FREEZE-TEST-021A",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                    "status": "review",
                    "review_notes_zh": "已審核通過",
                }
            ]
        }
        task = ai_status.get_task(state, "FREEZE-TEST-021A")
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.append_log"), unittest.mock.patch("ai_status.sync_all"):
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved):
                ai_status.command_approve(state, ["FREEZE-TEST-021A", "Approved"])
            self.assertEqual(task["last_approved_head"], approved)

            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"):
                ai_status.command_reopen(state, ["FREEZE-TEST-021A", "More work"])
                self.assertNotIn("approved_head", task)
                # B21's precondition: the durable record survives the reopen,
                # so restore can re-freeze it rather than producing no freeze.
                self.assertEqual(task["last_approved_head"], approved)

                # Owner pushes an unreviewed commit, then tries to restore.
                drifted = "bbbbbbbb22222222333333334444444455555555"
                with unittest.mock.patch("ai_status.resolve_task_sha", return_value=drifted):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_restore_approved(state, ["FREEZE-TEST-021A", "Restore"])
                self.assertIn("moved to bbbbbbbb", str(cm.exception))
                self.assertEqual(task["status"], "in_progress")

            # A task that never had a durable head cannot be restored at all.
            legacy = self._restore_state(id="FREEZE-TEST-021B")
            with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                 unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved):
                with self.assertRaises(SystemExit) as cm:
                    ai_status.command_restore_approved(legacy, ["FREEZE-TEST-021B", "Restore"])
            self.assertIn("no durable reviewer-approved head", str(cm.exception))
            self.assertEqual(ai_status.get_task(legacy, "FREEZE-TEST-021B")["status"], "in_progress")

    def test_restore_approved_refreezes_the_reviewed_head_when_branch_unmoved(self) -> None:
        """Positive control for the two refusals above: a genuinely spurious
        downgrade still recovers, and it recovers *with* the freeze intact."""
        approved = "1111111122222222333333334444444455555555"
        state = self._restore_state(id="FREEZE-TEST-021C", last_approved_head=approved)
        task = ai_status.get_task(state, "FREEZE-TEST-021C")
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=approved), \
             unittest.mock.patch("ai_status.append_log"):
            ai_status.command_restore_approved(state, ["FREEZE-TEST-021C", "Spurious downgrade"])
        self.assertEqual(task["status"], "review_approved")
        self.assertEqual(task["approved_head"], approved)

        # And the restored task is frozen: a later push is still refused.
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
             unittest.mock.patch(
                 "ai_status.collect_done_delivery_metadata",
                 return_value={
                     "verified_head": "bbbbbbbb22222222333333334444444455555555",
                     "pull_request": {
                         "head_sha": approved,
                         "merge_commit": "aaaaaaaa22222222333333334444444455555555",
                     },
                 },
             ) as collect, \
             unittest.mock.patch("ai_status.append_log"):
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_done(state, ["FREEZE-TEST-021C", "Finalize"])
        self.assertIn("differs from reviewer-approved head", str(cm.exception))
        collect.assert_called_once()

    def test_restore_approved_fails_closed_when_head_unresolvable(self) -> None:
        approved = "1111111122222222333333334444444455555555"
        for head, expected in (
            (None, "branch HEAD could not be resolved"),
            (RuntimeError("git down"), "unable to resolve the current branch HEAD"),
        ):
            with self.subTest(head=str(head)):
                state = self._restore_state(id="FREEZE-TEST-021D", last_approved_head=approved)
                ai_status.clear_ai_status_caches()
                patch = (
                    unittest.mock.patch("ai_status.resolve_task_sha", side_effect=head)
                    if isinstance(head, Exception)
                    else unittest.mock.patch("ai_status.resolve_task_sha", return_value=head)
                )
                with unittest.mock.patch("ai_status.current_actor_validated", return_value="Antigravity4"), \
                     patch, unittest.mock.patch("ai_status.append_log"):
                    with self.assertRaises(SystemExit) as cm:
                        ai_status.command_restore_approved(state, ["FREEZE-TEST-021D", "Restore"])
                self.assertIn(expected, str(cm.exception))
                self.assertEqual(
                    ai_status.get_task(state, "FREEZE-TEST-021D")["status"], "in_progress"
                )

    def test_restore_approved_head_requires_the_missing_head_shape(self) -> None:
        """It repairs the B22 shape only; it is not a second approve path."""
        head = "1111111122222222333333334444444455555555"
        ai_status.clear_ai_status_caches()
        with unittest.mock.patch("ai_status.current_actor_validated", return_value="Claude"), \
             unittest.mock.patch("ai_status.resolve_task_sha", return_value=head), \
             unittest.mock.patch("ai_status.task_pr_head_and_merge_commit", return_value=(head, None)), \
             unittest.mock.patch("ai_status.append_log"):
            in_progress = self._restore_state(id="FREEZE-TEST-022F")
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(in_progress, ["FREEZE-TEST-022F", head, "attest"])
            self.assertIn("only valid when status is review_approved", str(cm.exception))

            already = {"tasks": [self._missing_head_task("FREEZE-TEST-022G")]}
            ai_status.get_task(already, "FREEZE-TEST-022G")["approved_head"] = head
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(already, ["FREEZE-TEST-022G", head, "attest"])
            self.assertIn("already carries one", str(cm.exception))

            same_identity = {"tasks": [self._missing_head_task("FREEZE-TEST-022H")]}
            ai_status.get_task(same_identity, "FREEZE-TEST-022H")["owner"] = "Claude"
            with self.assertRaises(SystemExit) as cm:
                ai_status.command_restore_approved_head(same_identity, ["FREEZE-TEST-022H", head, "attest"])
            self.assertIn("must be separate identities", str(cm.exception))

    # ------------------------------------------------------------------
    # Round-8 non-blocking findings N1/N2, now covered.
    # ------------------------------------------------------------------

    def test_return_to_review_transitions_clear_and_preserve_the_right_heads(self) -> None:
        """N2: `reopen`/`handoff` popping approved_head is load-bearing.

        After B20-b a leftover approved_head makes `command_approve` refuse
        outright, so dropping either pop would wedge the whole
        reject -> rework -> re-approve cycle with the suite still green. The
        durable `last_approved_head` must survive all three, or B21's fix
        loses its input.
        """
        approved = "1111111122222222333333334444444455555555"

        def _state() -> dict[str, Any]:
            return {
                "tasks": [
                    {
                        "id": "FREEZE-TEST-023",
                        "owner": "Antigravity4",
                        "reviewer": "Claude",
                        "status": "review_approved",
                        "approved_head": approved,
                        "last_approved_head": approved,
                    }
                ],
                "handoffs": [],
            }

        cases = (
            ("reopen", ai_status.command_reopen, ["FREEZE-TEST-023", "rework"], "Antigravity4"),
            ("handoff", ai_status.command_handoff, ["FREEZE-TEST-023", "Claude", "review"], "Antigravity4"),
            ("re_review", ai_status.command_re_review, ["FREEZE-TEST-023", "re-review"], "Antigravity4"),
        )
        for label, command, args, actor in cases:
            with self.subTest(command=label):
                state = _state()
                with unittest.mock.patch("ai_status.current_actor_validated", return_value=actor), \
                     unittest.mock.patch("ai_status.resolve_actor_reference", side_effect=lambda v, **_: v), \
                     unittest.mock.patch("ai_status.append_log"):
                    command(state, args)
                task = ai_status.get_task(state, "FREEZE-TEST-023")
                self.assertNotIn("approved_head", task)
                self.assertEqual(task["last_approved_head"], approved)

    def test_ci_failure_branch_signals_and_clears_the_pending_timer(self) -> None:
        """N1: deleting the whole `ci_status == "failure"` branch kept the suite
        green because the catch-all still suppressed dispatch. Its distinguishing
        side effects -- the `ci_failed` signal and popping ci_pending_since_ts so
        a stale timer cannot fire the 30-minute escalation -- had no coverage."""
        approved = "1111111122222222333333334444444455555555"
        config = self._build_freeze_test_config()
        task = {
            "id": "FREEZE-TEST-024",
            "owner": "Antigravity4",
            "reviewer": "Claude",
            "status": "review_approved",
            "approved_head": approved,
            "ci_pending_since_ts": datetime.now(UTC).timestamp() - 4000,
        }

        dispatched, logged, mock_queue = self._run_finalize_dispatch_capturing_signals(
            config, task, head=approved, ci="failure"
        )
        self.assertFalse(dispatched)
        mock_queue.assert_not_called()
        self.assertIn("ci_failed", [e["type"] for e in logged])
        self.assertNotIn("ci_pending_timeout", [e["type"] for e in logged])
        self.assertNotIn("ci_pending_since_ts", task)
        self.assertIn("failed; resolve failing checks", task["next"])


class SuccessfulWorkerPostconditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()
        self.config.setdefault("provider_guardrails", {})["generic_exit_reassign_after"] = 2

    @staticmethod
    def _task(*, status: str = "in_progress", next_step: str = "Implement packet") -> dict[str, Any]:
        return {
            "id": "ODP-POSTCONDITION-001",
            "status": status,
            "owner": "Antigravity4",
            "reviewer": "Codex6",
            "next": next_step,
            "depends_on": [],
        }

    @staticmethod
    def _worker(
        task: dict[str, Any],
        *,
        run_id: str = "antigravity4-postcondition-1",
        reason: str = "owned_in_progress_dispatch",
        agent_id: str = "antigravity4",
        dispatch_head: str = "a" * 40,
    ) -> dict[str, Any]:
        dispatch_task = {
            "id": task["id"],
            "status": task["status"],
            "owner": task["owner"],
            "reviewer": task["reviewer"],
            "next": task["next"],
            "head": dispatch_head,
        }
        return {
            "run_id": run_id,
            "task_id": task["id"],
            "provider": agent_id,
            "agent_id": agent_id,
            "status": "running",
            "queue_event_id": f"evt-{run_id}",
            "pid": 999999,
            "runner_status": "completed",
            "exit_code": 0,
            "last_event_at": "2026-07-31T15:00:00Z",
            "request_snapshot": {
                "reason": reason,
                "metadata": {
                    "logical_agent_id": agent_id,
                    "task": dispatch_task,
                },
            },
        }

    def _poll(self, state: dict[str, Any], task: dict[str, Any], *, current_head: str) -> bool:
        with (
            mock.patch.object(supervisor, "load_approval_state", return_value={"pending": [], "history": []}),
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_provider_report", return_value={}),
            mock.patch.object(supervisor, "retry_due_workers", return_value=False),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_task_progress_head", return_value=current_head),
            mock.patch.object(supervisor, "detect_worker_failure", side_effect=AssertionError("zero-exit worker must use postcondition path")),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            return supervisor.poll_workers(self.config, state)

    def test_poll_accepts_new_head_as_incremental_progress_and_clears_failure_streak(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        self.assertTrue(self._poll(state, task, current_head="b" * 40))
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "incremental_progress")
        self.assertEqual(state["queue"]["events"][worker["queue_event_id"]]["status"], "completed")
        self.assertNotIn(streak_key, state["provider_guardrails"]["task_failure_streaks"])

    def test_poll_reassigns_after_repeated_zero_exit_without_progress(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        with mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Antigravity5") as reassign:
            self.assertTrue(self._poll(state, task, current_head="a" * 40))
        self.assertEqual(worker["status"], "reassigned")
        self.assertEqual(worker["reassigned_to"], "Antigravity5")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][streak_key]["count"], 2)
        reassign.assert_called_once()

    def test_owner_ready_note_without_handoff_is_no_progress_even_with_new_head(self) -> None:
        task = self._task(next_step="Implementation complete; ready for independent review")
        worker = self._worker(self._task())
        outcome = supervisor.successful_worker_exit_outcome(
            worker,
            task,
            terminal_statuses={"done", "review_approved"},
        )
        self.assertEqual(outcome, "no_progress")

    def test_poll_accepts_reviewer_reopen_as_durable_review_decision(self) -> None:
        dispatch_task = self._task(status="review", next_step="Independent review required")
        worker = self._worker(dispatch_task, reason="review_ready_dispatch", agent_id="codex6")
        current_task = self._task(status="in_progress", next_step="Fix review finding B1")
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
        }
        self.assertTrue(self._poll(state, current_task, current_head="a" * 40))
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["progress_outcome"], "review_decided")

    def test_poll_never_recounts_historical_terminal_run_after_reopen(self) -> None:
        task = self._task(status="review", next_step="Fresh exact-head review required")
        for historical_status in ("completed", "failed", "superseded", "reassigned"):
            with self.subTest(historical_status=historical_status):
                worker = self._worker(
                    self._task(status="review", next_step="Old review"),
                    reason="review_ready_dispatch",
                    agent_id="codex6",
                )
                worker.update(
                    {
                        "status": historical_status,
                        "runner_status": "failed",
                        "exit_code": -15,
                        "runner_signal": 15,
                    }
                )
                state = {
                    "queue": {"events": {worker["queue_event_id"]: {"status": "completed"}}},
                    "workers": {worker["run_id"]: worker},
                    "provider_guardrails": {"task_failure_streaks": {}},
                }
                with mock.patch.object(
                    supervisor,
                    "maybe_reassign_task_after_worker_failure",
                    side_effect=AssertionError("historical run must not be re-counted"),
                ):
                    self.assertFalse(self._poll(state, task, current_head="b" * 40))
                self.assertEqual(worker["status"], historical_status)
                self.assertEqual(state["provider_guardrails"]["task_failure_streaks"], {})

    def test_boot_reconciliation_applies_same_no_progress_threshold(self) -> None:
        task = self._task()
        worker = self._worker(task)
        streak_key = f"{task['id']}:antigravity4"
        state = {
            "queue": {"events": {worker["queue_event_id"]: {"status": "started"}}},
            "workers": {worker["run_id"]: worker},
            "provider_guardrails": {"task_failure_streaks": {streak_key: {"count": 1}}},
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor, "resolve_task_progress_head", return_value="a" * 40),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value="Antigravity5") as reassign,
        ):
            changed = supervisor.reconcile_runtime_on_boot(self.config, state)
        self.assertTrue(changed)
        self.assertEqual(worker["status"], "reassigned")
        self.assertEqual(worker["reassigned_to"], "Antigravity5")
        self.assertEqual(state["provider_guardrails"]["task_failure_streaks"][streak_key]["count"], 2)
        reassign.assert_called_once()


class SupervisorFailureLoopCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_full_agent_matrix_coverage_all_configured_agents(self) -> None:
        all_agents = [
            "Antigravity", "Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7",
            "Claude", "Claude2", "Claude3",
            "Codex", "Codex2", "Codex3", "Codex4", "Codex5", "Codex6", "Codex7", "Codex8", "Codex9", "CodexCoordinator",
            "Gemini", "Gemini2", "Copilot"
        ]
        for agent in all_agents:
            with self.subTest(agent=agent, role="owner"):
                fallbacks = supervisor.get_agent_reassignment_candidates(self.config, agent, role="owner")
                self.assertTrue(len(fallbacks) > 0, f"Owner fallbacks for {agent} should not be empty")
                self.assertNotIn(agent, fallbacks)
                self.assertNotIn("Human/Ops", fallbacks)

            with self.subTest(agent=agent, role="reviewer"):
                fallbacks = supervisor.get_agent_reassignment_candidates(self.config, agent, role="reviewer")
                self.assertTrue(len(fallbacks) > 0, f"Reviewer fallbacks for {agent} should not be empty")
                self.assertNotIn(agent, fallbacks)
                self.assertNotIn("Human/Ops", fallbacks)

    def test_dynamic_fallback_derivation_when_agent_missing_from_config(self) -> None:
        lean_config = {
            "worker_reassignment": {
                "enabled": True,
                "after_attempts": 2,
                "reassign_on_terminal_failure": True,
                "owner_fallbacks": {},
                "reviewer_fallbacks": {},
            },
            "agents": {
                "antigravity4": {"display_name": "Antigravity4"},
                "antigravity3": {"display_name": "Antigravity3"},
                "codex6": {"display_name": "Codex6"},
            },
        }
        fallbacks = supervisor.get_agent_reassignment_candidates(lean_config, "Antigravity4", role="owner")
        self.assertIn("Antigravity3", fallbacks)
        self.assertIn("Codex6", fallbacks)
        self.assertNotIn("Antigravity4", fallbacks)

    def test_failure_loop_reassignment_drill_owner(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-SITESCORE-OUTCOME-001",
            "agent_id": "antigravity4",
            "retry_count": 2,
            "run_id": "antigravity4-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-SITESCORE-OUTCOME-001",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Codex6",
                }
            ]
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "ODP-PLAN-SITESCORE-OUTCOME-001:antigravity4": {
                        "task_id": "ODP-PLAN-SITESCORE-OUTCOME-001",
                        "provider": "antigravity4",
                        "count": 2,
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Terminal model quota exhausted",
                terminal=True,
            )

        self.assertIsNotNone(reassigned_to)
        self.assertNotEqual(reassigned_to, "Antigravity4")
        self.assertNotEqual(reassigned_to, "Codex6")
        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "ODP-PLAN-SITESCORE-OUTCOME-001")
        self.assertEqual(kwargs["new_owner"], reassigned_to)
        self.assertEqual(kwargs["new_status"], "todo")
        self.assertNotIn("ODP-PLAN-SITESCORE-OUTCOME-001:antigravity4", state["provider_guardrails"]["task_failure_streaks"])

    def test_failure_loop_reassignment_drill_reviewer(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
            "agent_id": "antigravity7",
            "retry_count": 2,
            "run_id": "antigravity7-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
                    "status": "review",
                    "owner": "Codex6",
                    "reviewer": "Antigravity7",
                }
            ]
        }
        state = {
            "provider_guardrails": {
                "task_failure_streaks": {
                    "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001:antigravity7": {
                        "task_id": "ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001",
                        "provider": "antigravity7",
                        "count": 2,
                    }
                }
            }
        }

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Quota limit reached",
                terminal=True,
            )

        self.assertIsNotNone(reassigned_to)
        self.assertNotEqual(reassigned_to, "Antigravity7")
        self.assertNotEqual(reassigned_to, "Codex6")
        self.assertEqual(persist.call_args.kwargs["new_reviewer"], reassigned_to)

    def test_fail_closed_human_ops_gate_never_auto_reassigned(self) -> None:
        worker = {
            "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
            "agent_id": "Human/Ops",
            "retry_count": 5,
            "run_id": "human-run-1",
        }
        status = {
            "tasks": [
                {
                    "id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
                    "status": "in_progress",
                    "owner": "Human/Ops",
                    "reviewer": "CodexCoordinator",
                }
            ]
        }
        state = {}

        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
        ):
            reassigned_to = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "Human gate pending",
                terminal=True,
            )

        self.assertIsNone(reassigned_to)
        persist.assert_not_called()

    def test_fail_closed_never_reassigns_to_human_ops(self) -> None:
        config_with_human = dict(self.config)
        config_with_human["worker_reassignment"] = {
            "enabled": True,
            "after_attempts": 1,
            "reassign_on_terminal_failure": True,
            "owner_fallbacks": {
                "Antigravity4": ["Human/Ops", "Codex6"],
            },
        }
        worker = {
            "task_id": "T-TEST",
            "agent_id": "antigravity4",
            "retry_count": 2,
        }
        status = {
            "tasks": [
                {
                    "id": "T-TEST",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Claude",
                }
            ]
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned = supervisor.maybe_reassign_task_after_worker_failure(
                config_with_human,
                {},
                worker,
                "failure",
                terminal=True,
            )

        self.assertNotEqual(reassigned, "Human/Ops")
        if persist.called:
            self.assertNotEqual(persist.call_args.kwargs["new_owner"], "Human/Ops")
            self.assertNotEqual(persist.call_args.kwargs["new_reviewer"], "Human/Ops")

    def test_fail_closed_skips_paused_agents(self) -> None:
        state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "antigravity3": {"provider": "antigravity3", "blocked_until": "2099-01-01T00:00:00Z"},
                    "antigravity5": {"provider": "antigravity5", "blocked_until": "2099-01-01T00:00:00Z"},
                }
            }
        }
        worker = {
            "task_id": "T-PAUSE-TEST",
            "agent_id": "antigravity4",
            "retry_count": 2,
        }
        status = {
            "tasks": [
                {
                    "id": "T-PAUSE-TEST",
                    "status": "in_progress",
                    "owner": "Antigravity4",
                    "reviewer": "Codex6",
                }
            ]
        }
        with (
            mock.patch.object(supervisor, "load_status", return_value=status),
            mock.patch.object(supervisor, "persist_task_reassignment") as persist,
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            reassigned = supervisor.maybe_reassign_task_after_worker_failure(
                self.config,
                state,
                worker,
                "failure",
                terminal=True,
            )

        self.assertNotIn(reassigned, ["Antigravity3", "Antigravity5", "Antigravity4", "Codex6"])
        if reassigned is None:
            persist.assert_not_called()

    def test_concurrent_claim_main_loop_state_preservation(self) -> None:
        """R1: Prove concurrent claim/main-loop state save preserves live worker, reconciles event, and avoids double-dispatch."""
        import tempfile
        from pathlib import Path
        ai_status.clear_ai_status_caches()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            cfg = deepcopy(self.config)
            live_activity_path = ROOT_DIR / "ai-activity-log.jsonl"
            live_activity_before = (
                live_activity_path.read_bytes()
                if live_activity_path.exists()
                else None
            )
            isolated_paths = {
                "status_file": tmp_root / "ai-status.json",
                "activity_log": tmp_root / "ai-activity-log.jsonl",
                "current_work": tmp_root / "current-work.md",
                "dashboard": tmp_root / "docs-site" / "index.html",
                "state_file": tmp_root / ".orchestrator" / "state.json",
                "event_queue": tmp_root / ".orchestrator" / "event-queue.jsonl",
                "approval_queue": tmp_root / ".orchestrator" / "approval-queue.json",
                "sidecar_catalog": tmp_root / ".orchestrator" / "sidecar_catalog.json",
                "github_bus_state": tmp_root / ".orchestrator" / "github-bus-state.json",
                "github_webhook_events": tmp_root / ".orchestrator" / "github-webhook-events.jsonl",
                "github_relay_state": tmp_root / ".orchestrator" / "github-relay-state.json",
                "provider_capabilities": tmp_root / ".orchestrator" / "provider_capabilities.json",
                "claude_mcp_config": tmp_root / ".orchestrator" / "claude-approval-broker.mcp.json",
            }
            (tmp_root / ".orchestrator").mkdir(parents=True, exist_ok=True)
            (tmp_root / ".orchestrator" / "event-queue.jsonl").write_text("", encoding="utf-8")
            (tmp_root / ".orchestrator" / "state.json").write_text("{}", encoding="utf-8")
            cfg["paths"] = {key: str(value) for key, value in isolated_paths.items()}
            cfg.setdefault("worker_worktrees", {})["root"] = str(tmp_root / "worker-worktrees")
            cfg.setdefault("permission_broker", {})["allowed_workspace_roots"] = [str(tmp_root)]
            cfg.setdefault("watchdog", {})["state_file"] = str(tmp_root / ".orchestrator" / "watchdog-state.json")
            cfg.setdefault("watchdog", {})["metrics_file"] = str(tmp_root / ".orchestrator" / "watchdog-metrics.jsonl")

            status_todo = {
                "tasks": [
                    {
                        "id": "ODP-CONC-001",
                        "status": "todo",
                        "owner": "Antigravity4",
                        "reviewer": "Codex",
                    }
                ]
            }

            initial_state = supervisor.load_runtime_state(cfg)
            initial_state["seen_event_keys"] = {}
            with (
                mock.patch.object(supervisor, "load_status", return_value=status_todo),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            ):
                supervisor.dispatch_ready_tasks(cfg, initial_state, {}, agent_ids_override=["antigravity4"])
            supervisor.save_runtime_state(cfg, initial_state)

            main_loop_state = supervisor.load_runtime_state(cfg)
            evt_id = list(main_loop_state.get("queue", {}).get("events", {}).keys())[0]

            concurrent_worker = {
                "run_id": "antigravity4-conc-run-1",
                "agent_id": "antigravity4",
                "task_id": "ODP-CONC-001",
                "queue_event_id": evt_id,
                "pid": os.getpid(),
                "status": "running",
                "started_at": supervisor.utc_now(),
                "last_heartbeat_at": supervisor.utc_now(),
            }
            disk_state = supervisor.load_runtime_state(cfg)
            disk_state["workers"]["antigravity4-conc-run-1"] = concurrent_worker
            disk_state["queue"]["events"][evt_id]["status"] = "started"
            disk_state["queue"]["events"][evt_id]["run_id"] = "antigravity4-conc-run-1"
            supervisor.save_runtime_state(cfg, disk_state)
            # The main loop crosses the real locked save boundary before it may
            # process its stale queue snapshot. The concurrent run and started
            # event must be merged into its live view.
            supervisor.save_runtime_state(cfg, main_loop_state)
            main_loop_state = supervisor.load_runtime_state(cfg)

            status_in_prog = {
                "tasks": [
                    {
                        "id": "ODP-CONC-001",
                        "status": "in_progress",
                        "owner": "Antigravity4",
                        "reviewer": "Codex6",
                    }
                ]
            }

            with (
                mock.patch.object(supervisor, "load_status", return_value=status_in_prog),
                mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, "isolated")),
                mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, "isolated")),
                mock.patch.object(supervisor, "start_worker_for_request") as start_worker,
                mock.patch.object(supervisor, "sync_dispatched_task_status", return_value=True),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                supervisor.process_queue(cfg, main_loop_state, {})
            start_worker.assert_not_called()

            supervisor.save_runtime_state(cfg, main_loop_state)
            saved_disk = supervisor.load_runtime_state(cfg)

            self.assertIn("antigravity4-conc-run-1", saved_disk["workers"])
            self.assertEqual(saved_disk["workers"]["antigravity4-conc-run-1"]["pid"], os.getpid())

            evt_rec = saved_disk["queue"]["events"].get(evt_id, {})
            self.assertIn(evt_rec.get("status"), {"started", "manual_pending"})
            self.assertNotEqual(evt_rec.get("skip_reason"), "stale_dispatch_event")
            self.assertEqual(evt_rec.get("run_id"), "antigravity4-conc-run-1")

            with (
                mock.patch.object(supervisor, "load_status", return_value=status_in_prog),
                mock.patch.object(supervisor, "scan_live_worker_pids_by_agent", return_value={}),
            ):
                dispatched = supervisor.dispatch_ready_tasks(cfg, saved_disk, {})
                self.assertFalse(dispatched)

            self.assertTrue(Path(cfg["paths"]["state_file"]).is_relative_to(tmp_root))
            self.assertTrue(Path(cfg["paths"]["event_queue"]).is_relative_to(tmp_root))
            self.assertTrue(Path(cfg["worker_worktrees"]["root"]).is_relative_to(tmp_root))
            live_activity_after = (
                live_activity_path.read_bytes()
                if live_activity_path.exists()
                else None
            )
            self.assertEqual(live_activity_after, live_activity_before)

    def test_runtime_state_save_preserves_terminal_worker_and_queue_transitions(self) -> None:
        """R9: A stale active disk snapshot cannot revive a terminal run or queue record."""
        for terminal_status in ("completed", "failed"):
            with self.subTest(terminal_status=terminal_status), tempfile.TemporaryDirectory() as tmpdir:
                state_path = Path(tmpdir) / "state.json"
                cfg = deepcopy(self.config)
                cfg["paths"]["state_file"] = str(state_path)
                cfg["paths"]["event_queue"] = str(Path(tmpdir) / "event-queue.jsonl")

                run_id = f"run-{terminal_status}"
                event_id = f"event-{terminal_status}"
                Path(cfg["paths"]["event_queue"]).write_text(
                    json.dumps(
                        {
                            "event_id": event_id,
                            "task_id": "ODP-TERMINAL-001",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                disk_state = supervisor.load_runtime_state(cfg)
                disk_state["workers"][run_id] = {
                    "run_id": run_id,
                    "task_id": "ODP-TERMINAL-001",
                    "queue_event_id": event_id,
                    "pid": os.getpid(),
                    "status": "running",
                    "last_heartbeat_at": "2026-07-31T08:00:00Z",
                }
                disk_state["queue"]["events"][event_id] = {
                    "status": "started",
                    "run_id": run_id,
                }
                supervisor.save_runtime_state(cfg, disk_state)

                terminal_state = deepcopy(disk_state)
                terminal_state["workers"][run_id]["status"] = terminal_status
                terminal_state["workers"][run_id]["finished_at"] = "2026-07-31T08:01:00Z"
                terminal_state["queue"]["events"][event_id]["status"] = terminal_status
                terminal_state["queue"]["events"][event_id]["processed_at"] = "2026-07-31T08:01:00Z"
                supervisor.save_runtime_state(cfg, terminal_state)

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["workers"][run_id]["status"], terminal_status)
                self.assertEqual(persisted["queue"]["events"][event_id]["status"], terminal_status)

    def test_runtime_state_save_serializes_two_real_writers(self) -> None:
        """R10: Two processes that loaded the same snapshot both survive the locked transaction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            state_path = tmp_root / "state.json"
            cfg = deepcopy(self.config)
            cfg["paths"]["state_file"] = str(state_path)
            cfg["paths"]["event_queue"] = str(tmp_root / "event-queue.jsonl")
            supervisor.save_runtime_state(cfg, supervisor.load_runtime_state(cfg))

            ready_read, ready_write = os.pipe()
            start_read, start_write = os.pipe()
            child_pids: list[int] = []
            for index in (1, 2):
                pid = os.fork()
                if pid == 0:
                    os.close(ready_read)
                    os.close(start_write)
                    try:
                        child_state = supervisor.load_runtime_state(cfg)
                        run_id = f"run-{index}"
                        child_state.setdefault("workers", {})[run_id] = {
                            "run_id": run_id,
                            "task_id": f"ODP-CONCURRENT-{index}",
                            "pid": os.getpid(),
                            "status": "running",
                            "last_heartbeat_at": "2026-07-31T08:00:00Z",
                        }
                        os.write(ready_write, b"1")
                        os.read(start_read, 1)
                        supervisor.save_runtime_state(cfg, child_state)
                    except BaseException:
                        os._exit(1)
                    os._exit(0)
                child_pids.append(pid)

            os.close(ready_write)
            os.close(start_read)
            try:
                ready = b""
                while len(ready) < 2:
                    ready += os.read(ready_read, 2 - len(ready))
                self.assertEqual(ready, b"11")
                os.write(start_write, b"12")
                exit_codes = []
                for pid in child_pids:
                    _, wait_status = os.waitpid(pid, 0)
                    exit_codes.append(os.waitstatus_to_exitcode(wait_status))
            finally:
                os.close(ready_read)
                os.close(start_write)

            self.assertEqual(exit_codes, [0, 0])
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(persisted["workers"]), ["run-1", "run-2"])

    def test_fail_closed_human_gate_task_metadata_and_non_dispatchable(self) -> None:
        """R2: Prove human-gate metadata and non_dispatchable tasks fail-closed and return None without calling persist."""
        worker = {
            "task_id": "ODP-HG-TEST",
            "agent_id": "antigravity4",
            "retry_count": 3,
            "run_id": "ag4-run-hg",
        }
        cases = [
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "task_class": "human_gate"},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "human_required_roles": ["security_reviewer"]},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "gate_status": "pending_human_signoff"},
            {"id": "ODP-HG-TEST", "status": "in_progress", "owner": "Antigravity4", "reviewer": "Codex6", "non_dispatchable": True},
        ]
        for task_dict in cases:
            with self.subTest(case=task_dict):
                status = {"tasks": [task_dict]}
                with mock.patch.object(supervisor, "load_status", return_value=status), \
                     mock.patch.object(supervisor, "persist_task_reassignment") as persist:
                    res = supervisor.maybe_reassign_task_after_worker_failure(
                        self.config,
                        {},
                        worker,
                        "failure threshold exceeded",
                        terminal=True,
                    )
                    self.assertIsNone(res)
                    persist.assert_not_called()

    def test_full_agent_matrix_and_negative_viability_coverage(self) -> None:
        """R3: Prove enabled agent matrix coverage and negative viability checks."""
        enabled_agents = [
            "Antigravity", "Antigravity2", "Antigravity3", "Antigravity4", "Antigravity5", "Antigravity6", "Antigravity7",
            "Claude", "Claude2", "Claude3",
            "Codex", "Codex2", "Codex3", "Codex4", "Codex5", "Codex6", "Codex7", "Codex8", "Codex9", "CodexCoordinator",
            "Gemini", "Gemini2", "Copilot"
        ]
        for agent in enabled_agents:
            with self.subTest(agent=agent, role="owner"):
                candidates = supervisor.get_agent_reassignment_candidates(self.config, agent, role="owner")
                viable = supervisor.first_viable_agent(self.config, candidates, exclude={agent})
                self.assertIsNotNone(viable, f"Owner fallback for {agent} should return a viable candidate")
                self.assertNotEqual(viable, agent)
                self.assertNotEqual(viable, "Human/Ops")

            with self.subTest(agent=agent, role="reviewer"):
                candidates = supervisor.get_agent_reassignment_candidates(self.config, agent, role="reviewer")
                viable = supervisor.first_viable_agent(self.config, candidates, exclude={agent})
                self.assertIsNotNone(viable, f"Reviewer fallback for {agent} should return a viable candidate")
                self.assertNotEqual(viable, agent)
                self.assertNotEqual(viable, "Human/Ops")

        disabled_config = deepcopy(self.config)
        disabled_config.setdefault("agents", {})["antigravity3"] = {"display_name": "Antigravity3", "enabled": False}
        viable_dis = supervisor.first_viable_agent(disabled_config, ["Antigravity3"], exclude=set())
        self.assertIsNone(viable_dis)

        paused_state = {
            "provider_guardrails": {
                "dispatch_pauses": {
                    "antigravity3": {"provider": "antigravity3", "blocked_until": "2099-01-01T00:00:00Z"}
                }
            }
        }
        viable_pause = supervisor.first_viable_agent(self.config, ["Antigravity3"], exclude=set(), state=paused_state)
        self.assertIsNone(viable_pause)

        viable_human = supervisor.first_viable_agent(self.config, ["Human/Ops"], exclude=set())
        self.assertIsNone(viable_human)

    def test_status_check_http_422_failure_injection_outbox_transactional(self) -> None:
        """R13: Real isolated main retries the exact 422 payload without rolling back state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            status_file = tmp_root / "ai-status.json"
            activity_file = tmp_root / "ai-activity-log.jsonl"
            runtime_file = tmp_root / ".orchestrator" / "state.json"
            runtime_file.parent.mkdir(parents=True, exist_ok=True)
            backup_dir = tmp_root / ".orchestrator" / "worktree-dirt-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / "odp-plan-acceptance-real-exec-001-lease_blocked.patch"
            backup_file.write_text("patch content", encoding="utf-8")
            runtime_payload = {
                "workers": {
                    "run-422": {
                        "run_id": "run-422",
                        "task_id": "ODP-422-TEST-001",
                        "status": "running",
                        "queue_event_id": "event-422",
                    }
                },
                "queue": {
                    "events": {
                        "event-422": {
                            "status": "started",
                            "run_id": "run-422",
                        }
                    }
                },
            }
            runtime_file.write_text(
                json.dumps(runtime_payload, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime_before = runtime_file.read_bytes()
            state = {
                "agents": [
                    {
                        "name": "CodexCoordinator",
                        "capability_lane": [],
                        "status": "idle",
                        "current_task_ids": [],
                        "branch": "",
                        "next": "",
                        "last_update": None,
                    },
                    {
                        "name": "Codex2",
                        "capability_lane": [],
                        "status": "idle",
                        "current_task_ids": [],
                        "branch": "",
                        "next": "",
                        "last_update": None,
                    },
                ],
                "tasks": [
                    {
                        "id": "ODP-422-TEST-001",
                        "title": "Outbox transaction",
                        "phase": "execution-control",
                        "owner": "CodexCoordinator",
                        "reviewer": "Codex2",
                        "status": "review",
                        "depends_on": [],
                        "artifacts": [],
                        "acceptance": [],
                        "next": "Awaiting review",
                        "last_update": "2026-07-31T08:00:00Z",
                    }
                ],
                "handoffs": [
                    {
                        "task_id": "ODP-422-TEST-001",
                        "from": "CodexCoordinator",
                        "to": "Codex2",
                        "message": "Review exact head",
                        "status": "pending",
                        "created_at": "2026-07-31T08:00:00Z",
                    }
                ],
                "blockers": [],
                "workload": {},
                "workload_summary": {},
            }
            status_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            sha = "f1078e8b" + ("0" * 32)
            observed_status_at_post: list[str] = []
            post_results = [
                mock.MagicMock(
                    returncode=1,
                    stderr=f"HTTP 422 Unprocessable Entity: No commit found for SHA {sha}",
                    stdout="",
                ),
                mock.MagicMock(returncode=0, stderr="", stdout="created"),
            ]

            def fake_post(*_args: Any, **_kwargs: Any) -> Any:
                persisted = json.loads(status_file.read_text(encoding="utf-8"))
                observed_status_at_post.append(persisted["tasks"][0]["status"])
                return post_results.pop(0)

            live_activity = ROOT_DIR / "ai-activity-log.jsonl"
            live_activity_before = live_activity.read_bytes() if live_activity.exists() else None
            with (
                mock.patch.dict(os.environ, {"AI_NAME": "Codex2"}, clear=False),
                mock.patch.object(ai_status, "STATUS_ROOT", tmp_root),
                mock.patch.object(ai_status, "STATUS_FILE", status_file),
                mock.patch.object(ai_status, "LOG_FILE", activity_file),
                mock.patch.object(ai_status, "CURRENT_WORK_FILE", tmp_root / "current-work.md"),
                mock.patch.object(ai_status, "DOCS_SITE_DIR", tmp_root / "docs-site"),
                mock.patch.object(ai_status, "ORCHESTRATOR_STATE_FILE", runtime_file),
                mock.patch.object(ai_status, "DASHBOARD_BUNDLE_FILE", tmp_root / "dashboard-bundle.json"),
                mock.patch.object(ai_status, "resolve_task_sha", return_value=sha),
                mock.patch.object(ai_status, "get_repository_slug_safe", return_value="alfloop-dev/odayplus"),
                mock.patch.object(ai_status, "sync_all", side_effect=lambda state_arg: ai_status.save_state(state_arg)),
                mock.patch.object(ai_status.subprocess, "run", side_effect=fake_post),
            ):
                self.assertEqual(
                    ai_status.main(
                        [
                            "ai_status.py",
                            "reopen",
                            "ODP-422-TEST-001",
                            "Exact-head review rejected",
                        ]
                    ),
                    0,
                )
                failed_state = json.loads(status_file.read_text(encoding="utf-8"))
                failed_task = failed_state["tasks"][0]
                self.assertEqual(failed_task["status"], "in_progress")
                self.assertEqual(len(failed_task["status_check_outbox"]), 1)
                failed_payload = failed_task["status_check_outbox"][0]
                self.assertEqual(failed_payload["sha"], sha)
                self.assertEqual(failed_payload["state"], "failure")
                self.assertEqual(failed_payload["context"], "task-review-gate")
                self.assertIn("Review rejected or reopened", failed_payload["description"])
                self.assertIn("HTTP 422", failed_payload["last_error"])

                self.assertEqual(ai_status.main(["ai_status.py", "sync"]), 0)

            reconciled_state = json.loads(status_file.read_text(encoding="utf-8"))
            reconciled_task = reconciled_state["tasks"][0]
            self.assertNotIn("status_check_outbox", reconciled_task)
            history = reconciled_task["status_check_delivery_history"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["sha"], sha)
            self.assertEqual(history[0]["state"], "failure")
            self.assertEqual(observed_status_at_post, ["in_progress", "in_progress"])
            self.assertEqual(runtime_file.read_bytes(), runtime_before)
            self.assertTrue(backup_file.exists())
            self.assertEqual(backup_file.read_text(encoding="utf-8"), "patch content")
            live_activity_after = live_activity.read_bytes() if live_activity.exists() else None
            self.assertEqual(live_activity_after, live_activity_before)

    def test_last_update_only_change_does_not_stale_eligible_wake(self) -> None:
        """R8: operational notes must not invalidate an otherwise eligible wake."""
        task = {
            "id": "ODP-WAKE-STABLE-001",
            "status": "in_progress",
            "owner": "Antigravity7",
            "reviewer": "CodexCoordinator",
            "last_update": "2026-07-31T08:28:23Z",
            "depends_on": [],
        }
        task_map = {task["id"]: task}
        event = supervisor.build_dispatch_event(
            task,
            "Antigravity7",
            supervisor.REASON_OWNED_IN_PROGRESS,
            task_map,
        )
        event["event_key"] = event["key"]
        event["target_display_name"] = "Antigravity7"

        task["last_update"] = "2026-07-31T08:29:00Z"
        task["next"] = "Coordinator note added after wake queueing"

        self.assertIsNone(
            supervisor.stale_dispatch_skip_message(self.config, event, task_map)
        )

    def test_assignment_or_status_change_still_stales_wake(self) -> None:
        """R8/R12 negative matrix: every authority or dependency change invalidates."""
        base_task = {
            "id": "ODP-WAKE-STABLE-002",
            "status": "in_progress",
            "owner": "Antigravity7",
            "reviewer": "CodexCoordinator",
            "depends_on": [],
        }
        dependency = {
            "id": "ODP-WAKE-DEPENDENCY-001",
            "status": "done",
            "owner": "Codex",
            "reviewer": "Codex2",
            "depends_on": [],
        }
        mutations = {
            "owner": lambda task, task_map: task.update(owner="Antigravity4"),
            "reviewer": lambda task, task_map: task.update(reviewer="Codex6"),
            "status": lambda task, task_map: task.update(status="review"),
            "dependency_list": lambda task, task_map: task.update(depends_on=[dependency["id"]]),
            "dependency_state": lambda task, task_map: task_map[dependency["id"]].update(status="in_progress"),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                task = deepcopy(base_task)
                if name == "dependency_state":
                    task["depends_on"] = [dependency["id"]]
                task_map = {
                    task["id"]: task,
                    dependency["id"]: deepcopy(dependency),
                }
                event = supervisor.build_dispatch_event(
                    task,
                    "Antigravity7",
                    supervisor.REASON_OWNED_IN_PROGRESS,
                    task_map,
                )
                event["event_key"] = event["key"]
                event["target_display_name"] = "Antigravity7"

                mutate(task, task_map)
                message = supervisor.stale_dispatch_skip_message(
                    self.config,
                    event,
                    task_map,
                ) or ""
                self.assertTrue(
                    "no longer eligible" in message or "task state changed" in message,
                    message,
                )

    def test_resolve_task_sha_precedence_over_merged_pr(self) -> None:
        """Verify pushed task branch beats both stale PR and unpushed local HEAD."""
        task_id = "TEST-BRANCH-PRECEDENCE-001"
        ai_status.clear_ai_status_caches()

        def fake_run(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "ls-remote --heads origin" in cmd_str:
                remote_ref = f"refs/heads/task/{task_id}"
                return unittest.mock.Mock(
                    returncode=0,
                    stdout=f"{'2' * 40}\t{remote_ref}\n",
                )
            if "branch --show-current" in cmd_str:
                return unittest.mock.Mock(returncode=0, stdout=f"task/{task_id}\n")
            if "rev-parse HEAD" in cmd_str:
                return unittest.mock.Mock(returncode=0, stdout=f"{'3' * 40}\n")
            if "pr view" in cmd_str:
                return unittest.mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"headRefOid": "1" * 40}),
                )
            return unittest.mock.Mock(returncode=1, stdout="")

        with unittest.mock.patch("subprocess.run", side_effect=fake_run):
            resolved = ai_status.resolve_task_sha(task_id)
            self.assertEqual(resolved, "2" * 40)

    def test_resolve_task_sha_exact_length_validation(self) -> None:
        """Verify resolve_task_sha accepts exactly 40 or 64 hex chars and rejects 41, 63, or non-hex."""
        task_id = "TEST-SHA-LENGTH-VAL-001"

        sha_candidates = [
            ("a" * 40, True),   # 40 hex (SHA-1) -> valid
            ("f" * 64, True),   # 64 hex (SHA-256) -> valid
            ("b" * 41, False),  # 41 hex -> invalid
            ("c" * 63, False),  # 63 hex -> invalid
            ("g" * 40, False),  # 40 non-hex -> invalid
        ]

        for sha, is_valid in sha_candidates:
            ai_status.clear_ai_status_caches()

            def fake_ls_remote(cmd, current_sha=sha, **kwargs):
                cmd_str = " ".join(cmd)
                if "ls-remote --heads origin" in cmd_str:
                    remote_ref = f"refs/heads/task/{task_id}"
                    return unittest.mock.Mock(
                        returncode=0,
                        stdout=f"{current_sha}\t{remote_ref}\n",
                    )
                return unittest.mock.Mock(returncode=1, stdout="")

            with unittest.mock.patch("subprocess.run", side_effect=fake_ls_remote):
                resolved = ai_status.resolve_task_sha(task_id, force_refresh=True)
                if is_valid:
                    self.assertEqual(resolved, sha)
                else:
                    self.assertIsNone(resolved)

class ReleaseCompletedWorkerForClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_release_completed_worker_reopened_in_progress_and_review_lifecycle(self) -> None:
        task_id = "ODP-REOPEN-CLAIM-001"
        run_id = "codex6-run-reopen-1"
        status_reopened = {
            "tasks": [
                {
                    "id": task_id,
                    "status": "in_progress",
                    "owner": "Antigravity6",
                    "reviewer": "Codex6",
                }
            ]
        }
        receipt = {
            "status": "completed",
            "exit_code": 0,
            "signal": None,
            "run_id": run_id,
            "pid": 999999,
        }
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "logical_agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                    "queue_event_id": "evt-reopen-1",
                }
            },
            "queue": {
                "events": {
                    "evt-reopen-1": {
                        "status": "started",
                        "run_id": run_id,
                    }
                }
            },
        }
        with mock.patch.object(supervisor, "load_status", return_value=status_reopened), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "write_activity_log"):
            changed = supervisor.release_completed_worker_for_claim(
                self.config,
                state,
                agent_name="Codex6",
                task_id=task_id,
            )

        self.assertTrue(changed)
        self.assertEqual(state["workers"][run_id]["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-reopen-1"]["status"], "completed")

    def test_release_completed_worker_rejects_all_invalid_receipt_and_identity_cases(self) -> None:
        task_id = "ODP-REJECT-CLAIM-001"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}

        # Case 1: Live PID
        run_id_live = "codex6-run-live"
        state_live = {
            "workers": {
                run_id_live: {
                    "run_id": run_id_live,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": os.getpid(),
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "completed", "exit_code": 0, "run_id": run_id_live, "pid": os.getpid()}):
            changed_live = supervisor.release_completed_worker_for_claim(
                self.config, state_live, agent_name="Codex6", task_id=task_id
            )
        self.assertFalse(changed_live)

        # Case 2: Missing receipt file / path
        run_id_noreceipt = "codex6-run-noreceipt"
        state_noreceipt = {
            "workers": {
                run_id_noreceipt: {
                    "run_id": run_id_noreceipt,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_noreceipt, agent_name="Codex6", task_id=task_id))

        # Case 3: Malformed receipt (exit_code != 0)
        run_id_badexit = "codex6-run-badexit"
        state_badexit = {
            "workers": {
                run_id_badexit: {
                    "run_id": run_id_badexit,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "completed", "exit_code": 1, "run_id": run_id_badexit}):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_badexit, agent_name="Codex6", task_id=task_id))

        # Case 4: Non-terminal runner status in receipt
        run_id_nonterm = "codex6-run-nonterm"
        state_nonterm = {
            "workers": {
                run_id_nonterm: {
                    "run_id": run_id_nonterm,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "running", "exit_code": 0, "run_id": run_id_nonterm}):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_nonterm, agent_name="Codex6", task_id=task_id))

        # Case 5: Mismatched run_id
        run_id_mismatch = "codex6-run-mismatch"
        state_mismatch = {
            "workers": {
                run_id_mismatch: {
                    "run_id": run_id_mismatch,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "completed", "exit_code": 0, "run_id": "other-run-id"}):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_mismatch, agent_name="Codex6", task_id=task_id))

        # Case 6: Mismatched PID
        run_id_pid_mismatch = "codex6-run-pid-mismatch"
        state_pid_mismatch = {
            "workers": {
                run_id_pid_mismatch: {
                    "run_id": run_id_pid_mismatch,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "completed", "exit_code": 0, "run_id": run_id_pid_mismatch, "pid": 111111}):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_pid_mismatch, agent_name="Codex6", task_id=task_id))

        # Case 7: Forged cached terminal in worker dict without disk receipt
        run_id_forged = "codex6-run-forged"
        state_forged = {
            "workers": {
                run_id_forged: {
                    "run_id": run_id_forged,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "completed",
                    "pid": 999999,
                    "runner_status": "completed",
                    "exit_code": 0,
                }
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_forged, agent_name="Codex6", task_id=task_id))

        # Case 8: Multiple historical same task-agent worker records
        state_multi = {
            "workers": {
                "run-1": {
                    "run_id": "run-1",
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status1.json",
                },
                "run-2": {
                    "run_id": "run-2",
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 888888,
                    "runner_status_path": "/tmp/fake_status2.json",
                },
            }
        }
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value={"status": "completed", "exit_code": 0, "run_id": "run-1", "pid": 999999}):
            self.assertFalse(supervisor.release_completed_worker_for_claim(self.config, state_multi, agent_name="Codex6", task_id=task_id))


class ReleaseCompletedWorkerShapeCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_release_completed_worker_4_history_1_active_success(self) -> None:
        task_id = "ODP-4HIST-1ACT-SUCCESS"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        run_active = "run-active-5"
        state = {
            "workers": {
                "run-hist-1": {"run_id": "run-hist-1", "agent_id": "codex6", "task_id": task_id, "status": "completed"},
                "run-hist-2": {"run_id": "run-hist-2", "agent_id": "codex6", "task_id": task_id, "status": "failed"},
                "run-hist-3": {"run_id": "run-hist-3", "agent_id": "codex6", "task_id": task_id, "status": "killed"},
                "run-hist-4": {"run_id": "run-hist-4", "agent_id": "codex6", "task_id": task_id, "status": "abandoned"},
                run_active: {
                    "run_id": run_active,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status_active.json",
                    "queue_event_id": "evt-active-5",
                },
            },
            "queue": {
                "events": {
                    "evt-active-5": {"id": "evt-active-5", "status": "claimed", "run_id": run_active}
                }
            },
        }
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_active, "pid": 999999}
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "write_activity_log"):
            changed = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
        self.assertTrue(changed)
        self.assertEqual(state["workers"][run_active]["status"], "completed")
        self.assertEqual(state["queue"]["events"]["evt-active-5"]["status"], "completed")
        self.assertEqual(state["workers"]["run-hist-1"]["status"], "completed")
        self.assertEqual(state["workers"]["run-hist-2"]["status"], "failed")

    def test_release_completed_worker_two_active_rejection(self) -> None:
        task_id = "ODP-TWO-ACTIVE-REJECT"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        state = {
            "workers": {
                "run-active-1": {
                    "run_id": "run-active-1",
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999991,
                    "runner_status_path": "/tmp/fake_status1.json",
                },
                "run-active-2": {
                    "run_id": "run-active-2",
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999992,
                    "runner_status_path": "/tmp/fake_status2.json",
                },
            }
        }
        receipt = {"status": "completed", "exit_code": 0, "run_id": "run-active-1", "pid": 999991}
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False):
            changed = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
        self.assertFalse(changed)

    def test_release_completed_worker_missing_receipt_pid_rejection(self) -> None:
        task_id = "ODP-MISSING-RECEIPT-PID"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        run_id = "run-active-pid"
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id}
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False):
            changed = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
        self.assertFalse(changed)

    def test_release_completed_worker_missing_receipt_child_pid_rejection(self) -> None:
        task_id = "ODP-MISSING-RECEIPT-CHILD-PID"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        run_id = "run-active-child-pid"
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 999999,
                    "child_pid": 888888,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": 999999}
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False):
            changed = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
        self.assertFalse(changed)

    def test_release_completed_worker_pid_schema_validation_mocked(self) -> None:
        """Verify invalid PID values (bool, string, 0, negative, float) return False without calling pid_is_alive or raising."""
        task_id = "ODP-PID-SCHEMA-001"
        run_id = "run-schema-val-1"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": 1234}

        invalid_pids = [False, True, "1234", "invalid", 0, -1, -999, 12.34, [1234]]
        for bad_pid in invalid_pids:
            state = {
                "workers": {
                    run_id: {
                        "run_id": run_id,
                        "agent_id": "codex6",
                        "task_id": task_id,
                        "status": "running",
                        "pid": bad_pid,
                        "runner_status_path": "/tmp/fake_status.json",
                    }
                }
            }
            with mock.patch.object(supervisor, "load_status", return_value=status), \
                 mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
                 mock.patch.object(supervisor, "pid_is_alive") as mock_pid_alive:
                res = supervisor.release_completed_worker_for_claim(
                    self.config, state, agent_name="Codex6", task_id=task_id
                )
                self.assertFalse(res)
                mock_pid_alive.assert_not_called()

        # Check invalid receipt pid
        for bad_receipt_pid in invalid_pids:
            state = {
                "workers": {
                    run_id: {
                        "run_id": run_id,
                        "agent_id": "codex6",
                        "task_id": task_id,
                        "status": "running",
                        "pid": None,
                        "runner_status_path": "/tmp/fake_status.json",
                    }
                }
            }
            bad_receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": bad_receipt_pid}
            with mock.patch.object(supervisor, "load_status", return_value=status), \
                 mock.patch.object(supervisor, "_load_runtime_marker", return_value=bad_receipt), \
                 mock.patch.object(supervisor, "pid_is_alive") as mock_pid_alive:
                res = supervisor.release_completed_worker_for_claim(
                    self.config, state, agent_name="Codex6", task_id=task_id
                )
                self.assertFalse(res)
                mock_pid_alive.assert_not_called()

    def test_release_completed_worker_terminal_receipt_fail_closed_mocked(self) -> None:
        """Verify terminal receipt fields (exit_code=False, exit_code='0', signal, status, run_id) fail closed."""
        task_id = "ODP-RECEIPT-FAILCLOSED-001"
        run_id = "run-rcpt-fc-1"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 1234,
                    "runner_status_path": "/tmp/fake_status.json",
                }
            }
        }

        invalid_receipts = [
            {"status": "completed", "exit_code": False, "run_id": run_id, "pid": 1234},  # bool exit_code
            {"status": "completed", "exit_code": "0", "run_id": run_id, "pid": 1234},    # string exit_code
            {"status": "completed", "exit_code": 1, "run_id": run_id, "pid": 1234},      # non-zero exit_code
            {"status": "failed", "exit_code": 0, "run_id": run_id, "pid": 1234},         # non-completed status
            {"status": "completed", "exit_code": 0, "signal": "SIGKILL", "run_id": run_id, "pid": 1234},  # signal set
            {"status": "completed", "exit_code": 0, "signal": False, "run_id": run_id, "pid": 1234},     # signal=False
            {"status": "completed", "exit_code": 0, "runner_signal": False, "run_id": run_id, "pid": 1234}, # runner_signal=False
            {"status": "completed", "exit_code": 0, "run_id": 1234, "pid": 1234},        # numeric run_id
            {"status": "completed", "exit_code": 0, "worker_run_id": 1234, "pid": 1234}, # numeric worker_run_id
            {"status": "completed", "exit_code": 0, "run_id": "wrong-run-id", "pid": 1234},  # run_id mismatch
        ]

        for bad_rcpt in invalid_receipts:
            with mock.patch.object(supervisor, "load_status", return_value=status), \
                 mock.patch.object(supervisor, "_load_runtime_marker", return_value=bad_rcpt), \
                 mock.patch.object(supervisor, "pid_is_alive", return_value=False):
                res = supervisor.release_completed_worker_for_claim(
                    self.config, state, agent_name="Codex6", task_id=task_id
                )
                self.assertFalse(res)

    def test_release_completed_worker_atomic_rollback_on_queue_mutation_exception(self) -> None:
        """Verify worker and queue event record are restored byte-equivalent if finalize_queue_event_record mutates queue then raises."""
        import copy
        task_id = "ODP-ATOMIC-ROLLBACK-001"
        run_id = "run-atomic-1"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 1234,
                    "runner_status_path": "/tmp/fake_status.json",
                    "queue_event_id": "evt-atomic-1",
                }
            },
            "queue": {
                "events": {
                    "evt-atomic-1": {"id": "evt-atomic-1", "status": "claimed", "run_id": run_id}
                }
            },
        }
        original_worker = copy.deepcopy(state["workers"][run_id])
        original_queue_event = copy.deepcopy(state["queue"]["events"]["evt-atomic-1"])
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": 1234}

        def _mutating_finalize(config, st, w, status, error=None):
            st["queue"]["events"]["evt-atomic-1"]["status"] = "mutated_before_crash"
            raise RuntimeError("queue disk write error")

        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "finalize_queue_event_record", side_effect=_mutating_finalize):
            res = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
            self.assertFalse(res)
            # Verify atomic rollback of both worker and queue event record
            self.assertEqual(state["workers"][run_id], original_worker)
            self.assertEqual(state["queue"]["events"]["evt-atomic-1"], original_queue_event)

    def test_release_completed_worker_activity_log_exception_rollback(self) -> None:
        """Verify release_completed_worker_for_claim rolls back both worker and queue and returns False if write_activity_log raises."""
        import copy
        task_id = "ODP-LOG-EXCEPT-001"
        run_id = "run-log-except-1"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        state = {
            "workers": {
                run_id: {
                    "run_id": run_id,
                    "agent_id": "codex6",
                    "task_id": task_id,
                    "status": "running",
                    "pid": 1234,
                    "runner_status_path": "/tmp/fake_status.json",
                    "queue_event_id": "evt-log-except-1",
                }
            },
            "queue": {
                "events": {
                    "evt-log-except-1": {"id": "evt-log-except-1", "status": "claimed", "run_id": run_id}
                }
            },
        }
        original_worker = copy.deepcopy(state["workers"][run_id])
        original_queue_event = copy.deepcopy(state["queue"]["events"]["evt-log-except-1"])
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": 1234}
        with mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt), \
             mock.patch.object(supervisor, "pid_is_alive", return_value=False), \
             mock.patch.object(supervisor, "write_activity_log", side_effect=OSError("log lock failed")):
            res = supervisor.release_completed_worker_for_claim(
                self.config, state, agent_name="Codex6", task_id=task_id
            )
            self.assertFalse(res)
            # Verify atomic rollback of both worker and queue event record
            self.assertEqual(state["workers"][run_id], original_worker)
            self.assertEqual(state["queue"]["events"]["evt-log-except-1"], original_queue_event)

    def test_release_completed_worker_pid_schema_validation_unmocked_real_pid_is_alive(self) -> None:
        """Verify release_completed_worker_for_claim executes safely with real unmocked pid_is_alive on invalid PIDs."""
        task_id = "ODP-UNMOCKED-REAL-PID-001"
        run_id = "run-unmocked-1"
        status = {"tasks": [{"id": task_id, "status": "in_progress", "owner": "Antigravity6", "reviewer": "Codex6"}]}
        receipt = {"status": "completed", "exit_code": 0, "run_id": run_id, "pid": 1234}

        unmocked_cases = [False, 0, -100, "string_pid", 12.34]
        for bad_pid in unmocked_cases:
            state = {
                "workers": {
                    run_id: {
                        "run_id": run_id,
                        "agent_id": "codex6",
                        "task_id": task_id,
                        "status": "running",
                        "pid": bad_pid,
                        "runner_status_path": "/tmp/fake_status.json",
                    }
                }
            }
            with mock.patch.object(supervisor, "load_status", return_value=status), \
                 mock.patch.object(supervisor, "_load_runtime_marker", return_value=receipt):
                # Note: pid_is_alive is NOT mocked here!
                res = supervisor.release_completed_worker_for_claim(
                    self.config, state, agent_name="Codex6", task_id=task_id
                )
                self.assertFalse(res)


class ProcessQueueAgentOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_test_config()

    def test_process_queue_with_agent_override_only_processes_matching_agent_queue_events(self) -> None:
        events = [
            {
                "event_id": "evt-codex6-1",
                "task_id": "TASK-CODEX6",
                "target_agent": "Codex6",
                "key": "evt-codex6-1",
                "message": "test message codex6",
            },
            {
                "event_id": "evt-antigravity6-1",
                "task_id": "TASK-ANTIGRAVITY6",
                "target_agent": "Antigravity6",
                "key": "evt-antigravity6-1",
                "message": "test message antigravity6",
            },
            {
                "event_id": "evt-empty-target",
                "task_id": "TASK-EMPTY",
                "target_agent": "",
                "key": "evt-empty-target",
            },
            {
                "event_id": "evt-malformed-target",
                "task_id": "TASK-MALFORMED",
                "target_agent": "!!!###",
                "key": "evt-malformed-target",
            },
            {
                "event_id": "evt-unknown-target",
                "task_id": "TASK-UNKNOWN",
                "target_agent": "NonExistentAgent999",
                "key": "evt-unknown-target",
            },
        ]
        status = {
            "tasks": [
                {"id": "TASK-CODEX6", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-ANTIGRAVITY6", "status": "in_progress", "owner": "Antigravity6", "reviewer": "Claude"},
                {"id": "TASK-EMPTY", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-MALFORMED", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
                {"id": "TASK-UNKNOWN", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
            ]
        }
        state = {
            "queue": {
                "events": {
                    "evt-codex6-1": {"status": "pending"},
                    "evt-antigravity6-1": {"status": "pending"},
                    "evt-empty-target": {"status": "pending"},
                    "evt-malformed-target": {"status": "pending"},
                    "evt-unknown-target": {"status": "pending"},
                }
            }
        }
        with mock.patch.object(supervisor, "load_event_queue", return_value=events), \
             mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "current_provider_dispatch_pause", return_value=None), \
             mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None), \
             mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, "ok")), \
             mock.patch.object(supervisor, "check_worker_tree_clean", return_value=(True, "ok")), \
             mock.patch.object(supervisor, "start_worker_for_request", return_value=(True, "run-codex6-1", {})) as start_worker, \
             mock.patch.object(supervisor, "write_activity_log"):
            changed = supervisor.process_queue(
                self.config,
                state,
                provider_report={},
                agent_ids_override=["codex6"],
            )

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"]["evt-codex6-1"]["status"], "started")
        self.assertEqual(state["queue"]["events"]["evt-antigravity6-1"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-empty-target"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-malformed-target"]["status"], "pending")
        self.assertEqual(state["queue"]["events"]["evt-unknown-target"]["status"], "pending")
        start_worker.assert_called_once()

    def test_process_queue_with_agent_override_skips_targetless_events_without_inference(self) -> None:
        events = [
            {
                "event_id": "evt-targetless-complete",
                "task_id": "TASK-TARGETLESS",
                "key": "evt-targetless-complete",
                "message": "targetless complete message",
            },
        ]
        status = {
            "tasks": [
                {"id": "TASK-TARGETLESS", "status": "in_progress", "owner": "Codex6", "reviewer": "Claude"},
            ]
        }
        state = {
            "queue": {
                "events": {
                    "evt-targetless-complete": {"status": "pending"},
                }
            }
        }
        with mock.patch.object(supervisor, "load_event_queue", return_value=events), \
             mock.patch.object(supervisor, "load_status", return_value=status), \
             mock.patch.object(supervisor, "build_request") as mock_build_req:
            changed = supervisor.process_queue(
                self.config,
                state,
                provider_report={},
                agent_ids_override=["codex6"],
            )

        self.assertFalse(changed)
        self.assertEqual(state["queue"]["events"]["evt-targetless-complete"]["status"], "pending")
        mock_build_req.assert_not_called()

    def test_post_merge_deleted_remote_branch_finalize_dispatch(self) -> None:
        """B1 regression: when origin branch task/<id> is deleted on PR merge,
        resolve_task_checkout_sha resolves the post-merge checkout HEAD, enabling
        supervisor priority dispatch and reconciliation gates to issue owned_finalize_dispatch."""
        approved_head = "b664a8ea9fed476c6224a339994fa66163c574fa"
        checkout_head = "80ba278631111111222222223333333344444444"
        task_id = "B1-TEST-POST-MERGE-001"
        task_item = {
            "id": task_id,
            "status": "review_approved",
            "owner": "Antigravity",
            "reviewer": "Antigravity4",
            "approved_head": approved_head,
        }
        task_map = {task_id: task_item}

        with mock.patch("ai_status.resolve_task_sha", return_value=None), \
             mock.patch("ai_status.is_approved_head_satisfied", return_value=True), \
             mock.patch("ai_status.run_git_command") as mock_git, \
             mock.patch("ai_status.task_pr_ci_status", return_value=("CLOSED", "success")):
            def git_side_effect(cmd, **kwargs):
                if cmd[0] == "rev-parse" and cmd[1] == "HEAD":
                    return checkout_head
                if cmd[0] == "remote":
                    return "origin\n"
                if cmd[0] == "rev-parse" and cmd[1] == "--verify":
                    return checkout_head
                return ""
            mock_git.side_effect = git_side_effect

            # 1. Verify resolve_task_checkout_sha returns checkout_head despite resolve_task_sha returning None
            resolved = ai_status.resolve_task_checkout_sha(task_item, force_refresh=True)
            self.assertEqual(resolved, checkout_head)

            # 2. Verify dispatch_priority_for_task returns 1 (owned_finalize_dispatch priority)
            config = {"status_file": "/tmp/fake_status.json", "branch_workflow": {"dev_branch": "dev"}}
            prio = supervisor.dispatch_priority_for_task(config, task_item, "Antigravity", task_map=task_map)
            self.assertEqual(prio, 1)

            # 3. Verify dispatch_ready_tasks retains review_approved status and does not set approved_head_unresolved
            status = {"tasks": [task_item]}
            with mock.patch("supervisor.load_status", return_value=status), \
                 mock.patch("supervisor.load_event_queue", return_value=[]), \
                 mock.patch("supervisor.queue_delivery_event", return_value=False), \
                 mock.patch("supervisor.config_path", return_value="/tmp/fake_status.json"), \
                 mock.patch("supervisor.write_json"), \
                 mock.patch("supervisor.sync_status_pipeline"), \
                 mock.patch("supervisor.write_activity_log"):
                supervisor.dispatch_ready_tasks(config, {}, agent_ids_override=["antigravity"])
            self.assertEqual(task_item["status"], "review_approved")
            self.assertNotIn("Cannot verify branch HEAD", task_item.get("next", ""))
class QuarantineAndPreserveDirtyWorktreeTests(unittest.TestCase):

    def _create_git_repo_and_worktree(self, tmpdir_path: Path, task_id: str = "TASK-001") -> tuple[Path, Path, str]:
        repo_root = tmpdir_path / "main_repo"
        repo_root.mkdir()
        (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "dev"], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
        (repo_root / "README.md").write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)

        branch_name = f"task/{task_id}"
        subprocess.run(["git", "branch", branch_name], cwd=repo_root, check=True)

        wt_path = tmpdir_path / "workers" / supervisor._task_id_slug(task_id)
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)
        return repo_root, wt_path, branch_name

    def test_untracked_preservation_and_staged_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-REAL-001")

            staged_file = wt_path / "staged_doc.txt"
            staged_file.write_text("staged content", encoding="utf-8")
            subprocess.run(["git", "add", "staged_doc.txt"], cwd=wt_path, check=True)

            unstaged_file = wt_path / "README.md"
            unstaged_file.write_text("modified main\n", encoding="utf-8")

            untracked_file = wt_path / "untracked_output.log"
            untracked_file.write_text("untracked context data", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-REAL-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="unit_test",
            )

            self.assertTrue(ok)

            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertIn("A  staged_doc.txt", st_proc.stdout)
            self.assertIn("?? untracked_output.log", st_proc.stdout)

            self.assertTrue(staged_file.exists())
            self.assertEqual("staged content", staged_file.read_text(encoding="utf-8"))
            self.assertTrue(untracked_file.exists())
            self.assertEqual("untracked context data", untracked_file.read_text(encoding="utf-8"))

            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            self.assertTrue(backups_dir.exists())
            task_backups = list(backups_dir.glob("task-real-001-*"))
            self.assertEqual(1, len(task_backups))
            b_dir = task_backups[0]
            manifest_file = b_dir / "manifest.json"
            self.assertTrue(manifest_file.exists())
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual("TASK-REAL-001", manifest["task_id"])
            paths_in_manifest = [f["path"] for f in manifest["files"]]
            self.assertIn("staged_doc.txt", paths_in_manifest)
            self.assertIn("untracked_output.log", paths_in_manifest)
            self.assertTrue((b_dir / "untracked" / "untracked_output.log").exists())
            self.assertTrue((b_dir / "backup_checksums.sha256").exists())

    def test_backup_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-FAIL-001")

            untracked_file = wt_path / "untracked_data.txt"
            untracked_file.write_text("important data", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            with mock.patch("shutil.copy2", side_effect=OSError("Disk full")):
                ok = supervisor._quarantine_and_preserve_dirty_worktree(
                    config,
                    state,
                    wt_path,
                    "TASK-FAIL-001",
                    expected_branch=branch_name,
                    run_id=None,
                    trigger="unit_test",
                )

            self.assertFalse(ok)
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertIn("untracked_data.txt", st_proc.stdout)
            self.assertTrue(untracked_file.exists())

    def test_ref_and_branch_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-MISMATCH-001")

            (wt_path / "file.txt").write_text("dirt", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-MISMATCH-001",
                expected_branch="task/OTHER-BRANCH",
                run_id=None,
                trigger="unit_test",
            )
            self.assertFalse(ok)

    def test_active_run_exclusion_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-ACTIVE-001")

            (wt_path / "file.txt").write_text("dirt", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state = {
                "workers": {
                    "w1": {
                        "run_id": "run-active-123",
                        "task_id": "TASK-ACTIVE-001",
                        "status": "running",
                        "workspace_path": str(wt_path),
                    }
                }
            }

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-ACTIVE-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="lease_recovery",
            )
            self.assertFalse(ok)

            ok_self = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-ACTIVE-001",
                expected_branch=branch_name,
                run_id="run-active-123",
                trigger="worker_failed",
            )
            self.assertTrue(ok_self)

    def test_exact_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-RESTORE-001")

            untracked_file = wt_path / "sub" / "data.csv"
            untracked_file.parent.mkdir()
            untracked_file.write_text("col1,col2\nval1,val2\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-RESTORE-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="unit_test",
            )

            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            b_dir = list(backups_dir.glob("task-restore-001-*"))[0]

            target_restore_dir = tmp_p / "restored_location"
            restored_ok, msg = supervisor.restore_quarantined_worktree_backup(b_dir, target_restore_dir)
            self.assertTrue(restored_ok)
            restored_file = target_restore_dir / "sub" / "data.csv"
            self.assertTrue(restored_file.exists())
            self.assertEqual("col1,col2\nval1,val2\n", restored_file.read_text(encoding="utf-8"))

    def test_exact_restoration_mixed_staged_unstaged_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root, wt_path, branch_name = self._create_git_repo_and_worktree(tmp_p, "TASK-RESTORE-MIXED-001")

            # 1. Staged addition
            staged_add = wt_path / "staged_add.txt"
            staged_add.write_text("staged addition", encoding="utf-8")
            subprocess.run(["git", "add", "staged_add.txt"], cwd=wt_path, check=True)

            # 2. Staged modification + unstaged modification (MM)
            readme = wt_path / "README.md"
            readme.write_text("staged readme edit\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=wt_path, check=True)
            readme.write_text("staged readme edit\nunstaged readme edit\n", encoding="utf-8")

            # 3. Untracked file
            untracked = wt_path / "untracked.log"
            untracked.write_text("untracked log data", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            state: dict = {}

            ok = supervisor._quarantine_and_preserve_dirty_worktree(
                config,
                state,
                wt_path,
                "TASK-RESTORE-MIXED-001",
                expected_branch=branch_name,
                run_id=None,
                trigger="unit_test",
            )
            self.assertTrue(ok)

            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            b_dir = list(backups_dir.glob("task-restore-mixed-001-*"))[0]

            # Create target clean git worktree at the original head
            target_repo = tmp_p / "target_repo"
            target_repo.mkdir()
            subprocess.run(["git", "init", "-b", "dev"], cwd=target_repo, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=target_repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=target_repo, check=True)
            (target_repo / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=target_repo, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=target_repo, check=True)

            restored_ok, msg = supervisor.restore_quarantined_worktree_backup(b_dir, target_repo)
            self.assertTrue(restored_ok, msg)

            status_proc = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=target_repo,
                capture_output=True,
                text=True,
                check=True,
            )
            lines = sorted(status_proc.stdout.strip().splitlines())
            self.assertEqual(len(lines), 3)
            self.assertTrue(any(line.startswith("A  staged_add.txt") for line in lines))
            self.assertTrue(any(line.startswith("MM README.md") for line in lines))
            self.assertTrue(any(line.startswith("?? untracked.log") for line in lines))

    def test_prepare_worker_workspace_recovers_dirty_worktree_lease_with_real_bare_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-REMOTE-BARE-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)

            # Create an unpushed local commit. Recovery must lease the immutable
            # remote task head, never this newer mutable local branch ref.
            (wt_path / "local_only.txt").write_text("unpushed local commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "local_only.txt"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "local unpushed task commit"], cwd=wt_path, check=True)
            local_unpushed_head = supervisor._git_commit_oid(wt_path, "HEAD")

            # Introduce uncommitted dirty changes into the reused worker worktree
            dirty_file = wt_path / "dirty_work.txt"
            dirty_file.write_text("uncommitted progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "dirty_work.txt"], cwd=wt_path, check=True)

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            # Execute real prepare_worker_workspace without mocking refresh or quarantine
            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-remote-recover",
                target_agent="Codex",
            )

            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            self.assertNotEqual(leased_path, wt_path)

            # Confirm original worktree wt_path is untouched and still dirty
            self.assertTrue(dirty_file.exists())
            st_orig = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertTrue(st_orig.stdout.strip())

            # Confirm leased fresh worktree is clean
            st_leased = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_leased.stdout.strip())

            # Confirm bare remote task branch was NOT polluted with dirty commits
            remote_task_head = subprocess.run(
                ["git", "rev-parse", f"refs/heads/{branch_name}"],
                cwd=remote_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            leased_head = supervisor._git_commit_oid(leased_path, "HEAD")
            self.assertEqual(leased_head, remote_task_head)
            self.assertNotEqual(leased_head, local_unpushed_head)

            # Confirm backup manifest exists
            backups_dir = repo_root / ".orchestrator" / "worktree-dirt-backups"
            self.assertTrue(backups_dir.exists())
            self.assertGreater(len(list(backups_dir.glob("task-remote-bare-001-*"))), 0)

    def test_rejected_push_has_no_head_mismatch_and_no_unknown_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-PUSH-REJECT-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            # Configure pre-receive hook to reject all subsequent pushes
            hook_file = remote_root / "hooks" / "pre-receive"
            hook_file.write_text("#!/bin/sh\necho 'Push rejected' >&2\nexit 1\n", encoding="utf-8")
            hook_file.chmod(0o755)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            # Add dirt to worktree
            (wt_path / "secret.txt").write_text("abandoned dirty secret\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-push-reject",
                target_agent="Codex",
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_proc.stdout.strip())

            ref_ok, ref_status = supervisor._refresh_reused_worker_worktree(repo_root, leased_path, "origin/dev", branch_name)
            self.assertTrue(ref_ok)
            self.assertNotEqual("task_head_mismatch", ref_status)

    def test_dirty_worktree_quarantine_and_restoration_exhaustive_nul_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "tracked_a.txt").write_text("line 1\n", encoding="utf-8")
            (repo_root / "tracked_b.txt").write_text("line 2\n", encoding="utf-8")
            (repo_root / "to_delete.txt").write_text("delete me\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)

            wt_dir = tmp_p / "wt"
            subprocess.run(["git", "worktree", "add", "-b", "task/NUL-SAFE-001", str(wt_dir), "dev"], cwd=repo_root, capture_output=True, check=True)

            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (wt_dir / "tracked_a.txt").write_text("line 1 modified staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked_a.txt"], cwd=wt_dir, check=True)

            (wt_dir / "tracked_b.txt").write_text("line 2 modified unstaged\n", encoding="utf-8")
            (wt_dir / "to_delete.txt").unlink()

            quoted_name = "space & 'quote' file.txt"
            (wt_dir / quoted_name).write_text("complex filename content\n", encoding="utf-8")

            symlink_name = "link_to_a.txt"
            os.symlink("tracked_a.txt", wt_dir / symlink_name)

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                }
            }
            state: dict = {}

            preserved = supervisor._quarantine_and_preserve_dirty_worktree(
                config, state, wt_dir, "NUL-SAFE-001", expected_branch="task/NUL-SAFE-001", trigger="test"
            )
            self.assertTrue(preserved)

            backups = list((repo_root / ".orchestrator" / "worktree-dirt-backups").glob("nul-safe-001-*"))
            self.assertEqual(len(backups), 1)
            backup_dir = backups[0]

            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["task_id"], "NUL-SAFE-001")
            files_by_path = {f["path"]: f for f in manifest["files"]}

            self.assertIn("tracked_a.txt", files_by_path)
            self.assertIn("tracked_b.txt", files_by_path)
            self.assertIn("to_delete.txt", files_by_path)
            self.assertIn(quoted_name, files_by_path)
            self.assertIn(symlink_name, files_by_path)

            self.assertTrue(files_by_path[symlink_name]["is_symlink"])
            self.assertEqual(files_by_path[symlink_name]["symlink_target"], "tracked_a.txt")

            # Restore into a fresh target directory
            target_dir = tmp_p / "restored_target"
            subprocess.run(["git", "clone", str(repo_root), str(target_dir)], capture_output=True, check=True)
            subprocess.run(["git", "checkout", "task/NUL-SAFE-001"], cwd=target_dir, check=True)

            restored_ok, restored_msg = supervisor.restore_quarantined_worktree_backup(backup_dir, target_dir)
            self.assertTrue(restored_ok)
            self.assertEqual(restored_msg, "restored")

            self.assertEqual((target_dir / "tracked_a.txt").read_text(encoding="utf-8"), "line 1 modified staged\n")
            self.assertEqual((target_dir / "tracked_b.txt").read_text(encoding="utf-8"), "line 2 modified unstaged\n")
            self.assertFalse((target_dir / "to_delete.txt").exists())
            self.assertEqual((target_dir / quoted_name).read_text(encoding="utf-8"), "complex filename content\n")
            self.assertTrue(os.path.islink(target_dir / symlink_name))

    def test_quarantine_restoration_negative_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            target = tmp_p / "target"
            target.mkdir()

            # Missing manifest & checksums
            ok, msg = supervisor.restore_quarantined_worktree_backup(tmp_p, target)
            self.assertFalse(ok)
            self.assertIn("missing manifest", msg)

            # Corrupted checksum
            b_dir = tmp_p / "backup"
            b_dir.mkdir()
            (b_dir / "manifest.json").write_text('{"files":[]}', encoding="utf-8")
            (b_dir / "backup_checksums.sha256").write_text('{"missing.txt": "1234"}', encoding="utf-8")

            ok, msg = supervisor.restore_quarantined_worktree_backup(b_dir, target)
            self.assertFalse(ok)
            self.assertIn("corrupted backup: missing", msg)

    def test_context_materialization_ephemeral_and_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            wt_path = tmp_p / "wt"
            subprocess.run(["git", "worktree", "add", "-b", "task/EPHEM-001", str(wt_path), "dev"], cwd=repo_root, capture_output=True, check=True)

            config = {"paths": {"status_file": str(repo_root / "ai-status.json")}}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="EPHEM-001",
                reason="owned_in_progress_dispatch",
                context_files=["AI_COLLABORATION_GUIDE.md", ".orchestrator/task-briefs/ephem_001.md"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)
            self.assertIn("AI_COLLABORATION_GUIDE.md", materialized)
            self.assertIn(".orchestrator/task-briefs/ephem_001.md", materialized)

            # Confirm git exclude path was updated via rev-parse
            rc, out = supervisor._git_output(wt_path, "rev-parse", "--git-path", "info/exclude")
            self.assertEqual(rc, 0)
            ex_path = Path(out.strip())
            if not ex_path.is_absolute():
                ex_path = (wt_path / ex_path).resolve()
            ex_content = ex_path.read_text(encoding="utf-8")
            self.assertIn("AI_COLLABORATION_GUIDE.md", ex_content)
            self.assertIn(".orchestrator/task-briefs/", ex_content)

            # Confirm dirt classification treats context files as clean or scratch_only
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=wt_path, capture_output=True, check=True)
            classification, paths = supervisor._classify_worktree_dirt(st_proc.stdout)
            self.assertIn(classification, ("clean", "scratch_only"))

            # Add an unknown user file in task-briefs directory
            unknown_user_file = wt_path / ".orchestrator" / "task-briefs" / "my_custom_notes.txt"
            unknown_user_file.write_text("custom user notes\n", encoding="utf-8")

            # Confirm _restore_reusable_scratch does NOT delete unknown user file
            supervisor._restore_reusable_scratch(wt_path, paths)
            self.assertTrue(unknown_user_file.exists())
            self.assertEqual(unknown_user_file.read_text(encoding="utf-8"), "custom user notes\n")

    def test_original_worktree_byte_branch_gitdir_identity_on_lease_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("base content\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)

            task_id = "TASK-IDENTITY-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            (repo_root / "task_code.py").write_text("print('task')\n", encoding="utf-8")
            subprocess.run(["git", "add", "task_code.py"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "task commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "master"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            # Introduce dirty changes into wt_path
            dirty_f = wt_path / "dirty_progress.py"
            dirty_f.write_text("import sys\n# work in progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "dirty_progress.py"], cwd=wt_path, check=True)

            # Snapshot original bytes, branch, .git file, gitdir before recovery
            orig_git_file_content = (wt_path / ".git").read_text(encoding="utf-8")
            orig_dirty_content = dirty_f.read_text(encoding="utf-8")
            orig_branch = supervisor._git_output(wt_path, "symbolic-ref", "--quiet", "--short", "HEAD")[1]

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "master"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "master",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-identity",
                target_agent="Codex",
            )

            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            self.assertNotEqual(leased_path, wt_path)

            # Verify original wt_path bytes, branch, .git file content are 100% byte-identical
            self.assertEqual((wt_path / ".git").read_text(encoding="utf-8"), orig_git_file_content)
            self.assertEqual(dirty_f.read_text(encoding="utf-8"), orig_dirty_content)
            self.assertEqual(supervisor._git_output(wt_path, "symbolic-ref", "--quiet", "--short", "HEAD")[1], orig_branch)

            # Verify leased_path is clean and at exact task HEAD
            st_leased = subprocess.run(["git", "status", "--porcelain=v1"], cwd=leased_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_leased.stdout.strip())
            self.assertEqual(supervisor._git_commit_oid(leased_path, "HEAD"), supervisor._git_commit_oid(repo_root, branch_name))

    def test_dirty_lease_recovery_preserves_exact_task_sha_without_dev_merge(self) -> None:
        """B1 regression test: lease recovery preserves exact task SHA without merging origin/dev base."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "base.txt").write_text("initial base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-B1-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            (repo_root / "task.txt").write_text("task work\n", encoding="utf-8")
            subprocess.run(["git", "add", "task.txt"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "task commit"], cwd=repo_root, check=True)
            task_sha = supervisor._git_commit_oid(repo_root, branch_name)
            self.assertIsNotNone(task_sha)

            # Advance dev branch with new commits so task_sha becomes an ancestor of dev
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)
            (repo_root / "dev_advance.txt").write_text("dev advanced\n", encoding="utf-8")
            subprocess.run(["git", "add", "dev_advance.txt"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "dev advance commit"], cwd=repo_root, check=True)
            dev_sha = supervisor._git_commit_oid(repo_root, "dev")
            self.assertNotEqual(task_sha, dev_sha)

            # Create existing dirty worktree for task branch
            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            (wt_path / "dirty.txt").write_text("dirty uncommitted work\n", encoding="utf-8")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
            )

            ok, message = supervisor.prepare_worker_workspace(
                config, state, request, queue_event_id="evt-b1", target_agent="Antigravity"
            )
            self.assertTrue(ok)
            leased_path = Path(request.metadata["workspace_path"])
            leased_sha = supervisor._git_commit_oid(leased_path, "HEAD")

            # B1 check: fresh HEAD must be exact task_sha, NOT dev_sha
            self.assertEqual(leased_sha, task_sha)
            self.assertNotEqual(leased_sha, dev_sha)

    def test_materialized_context_written_to_actual_git_exclude_path(self) -> None:
        """B2 regression test: context exclusions written to git rev-parse --git-path info/exclude."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "wt"
            subprocess.run(["git", "worktree", "add", "-b", "task/EPHEM-B2", str(wt_path), "dev"], capture_output=True, check=True, cwd=repo_root)

            config = {"paths": {"status_file": str(repo_root / "ai-status.json")}}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="EPHEM-B2",
                reason="owned_in_progress_dispatch",
                context_files=["AI_COLLABORATION_GUIDE.md", ".orchestrator/task-briefs/ephem_b2.md"],
            )

            supervisor.materialize_worker_context_files(config, request, wt_path)

            # B2 check: git check-ignore must return 0 (ignored) for materialized context
            chk_proc = subprocess.run(["git", "check-ignore", "AI_COLLABORATION_GUIDE.md"], cwd=wt_path, capture_output=True, check=False)
            self.assertEqual(chk_proc.returncode, 0)

            # Confirm git status does NOT report AI_COLLABORATION_GUIDE.md as untracked
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertNotIn("AI_COLLABORATION_GUIDE.md", st_proc.stdout)

    def test_tracked_owner_content_classified_real_dirt_and_preserved(self) -> None:
        """B3 regression test: modified tracked context files classified as real dirt, quarantined, and recovered cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"baseline"}\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "B3-001"
            branch_name = f"task/{task_id}"
            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", "-b", branch_name, str(wt_path), "dev"], capture_output=True, check=True, cwd=repo_root)

            # Modify tracked ai-activity-log.jsonl (simulating owner progress)
            owner_progress_text = '{"event":"baseline"}\n{"event":"owner_progress"}\n'
            (wt_path / "ai-activity-log.jsonl").write_text(owner_progress_text, encoding="utf-8")

            # B3 check 1: dirt classification for tracked context file MUST be real
            st_proc = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=wt_path, capture_output=True, check=True)
            classification, paths = supervisor._classify_worktree_dirt(st_proc.stdout)
            self.assertEqual(classification, "real")
            self.assertEqual(paths, [])

            # B3 check 2: end-to-end prepare_worker_workspace interaction
            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json", "ai-activity-log.jsonl"],
            )

            ok, message = supervisor.prepare_worker_workspace(
                config, state, request, queue_event_id="evt-b3", target_agent="Antigravity"
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            # Fresh clean workspace must be allocated at a distinct path
            self.assertNotEqual(leased_path, wt_path)

            # Original dirty worktree bytes must remain 100% byte-identical and untouched
            self.assertEqual((wt_path / "ai-activity-log.jsonl").read_text(encoding="utf-8"), owner_progress_text)

    def test_materialization_refuses_to_overwrite_tracked_files_with_differing_source_bytes(self) -> None:
        """B4 regression test: materialization refuses to overwrite any Git-tracked destination even when live source differs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "repo"
            subprocess.run(["git", "init", str(repo_root)], capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)

            # Create tracked files on dev: ai-status.json, ai-activity-log.jsonl, and a task brief
            (repo_root / "ai-status.json").write_text('{"project":"tracked_baseline"}\n', encoding="utf-8")
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"tracked_log_baseline"}\n', encoding="utf-8")
            tb_dir = repo_root / ".orchestrator" / "task-briefs"
            tb_dir.mkdir(parents=True, exist_ok=True)
            tracked_brief = tb_dir / "b4_task_001.md"
            tracked_brief.write_text("# Tracked Brief Baseline\n", encoding="utf-8")
            (repo_root / "README.md").write_text("base readme\n", encoding="utf-8")

            subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial tracked baseline"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)

            task_id = "B4-TASK-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)

            # Now modify live status_root files (repo_root) to differ from the tracked baseline
            (repo_root / "ai-activity-log.jsonl").write_text('{"event":"live_canonical_log_differs"}\n', encoding="utf-8")
            tracked_brief.write_text("# Live Brief Modified Bytes\n", encoding="utf-8")
            (repo_root / "AI_COLLABORATION_GUIDE.md").write_text("# Live Collaboration Guide\n", encoding="utf-8")

            # Checkout dev in repo_root so task branch can be checked out in worktree
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "wt_b4"
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
            }
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=[
                    "AI_COLLABORATION_GUIDE.md",
                    "ai-activity-log.jsonl",
                    ".orchestrator/task-briefs/b4_task_001.md",
                    "ai-status.json",
                ],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            # 1. Untracked file AI_COLLABORATION_GUIDE.md was materialized
            self.assertIn("AI_COLLABORATION_GUIDE.md", materialized)

            # 2. Tracked files MUST NOT be in materialized list, and MUST NOT be overwritten
            self.assertNotIn("ai-activity-log.jsonl", materialized)
            self.assertNotIn(".orchestrator/task-briefs/b4_task_001.md", materialized)
            self.assertNotIn("ai-status.json", materialized)

            self.assertEqual((wt_path / "ai-activity-log.jsonl").read_text(encoding="utf-8"), '{"event":"tracked_log_baseline"}\n')
            self.assertEqual((wt_path / ".orchestrator" / "task-briefs" / "b4_task_001.md").read_text(encoding="utf-8"), "# Tracked Brief Baseline\n")
            self.assertEqual((wt_path / "ai-status.json").read_text(encoding="utf-8"), '{"project":"tracked_baseline"}\n')

            # 3. git status MUST remain 100% clean after materialization
            st_proc = subprocess.run(["git", "status", "--porcelain=v1"], cwd=wt_path, capture_output=True, text=True, check=True)
            self.assertEqual("", st_proc.stdout.strip())

    def test_materialize_worker_context_files_skips_symlinks_and_unsafe_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            repo_root = tmp_p / "main_repo"
            repo_root.mkdir()
            (repo_root / "ai-status.json").write_text('{"project":"canonical"}', encoding="utf-8")

            wt_path = tmp_p / "wt_b5"
            wt_path.mkdir()

            # Create target file README.md
            readme = wt_path / "README.md"
            readme.write_text("original owner readme content\n", encoding="utf-8")

            # Create untracked symlink ai-status.json -> README.md
            symlink = wt_path / "ai-status.json"
            symlink.symlink_to("README.md")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id="TASK-B5-001",
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            self.assertNotIn("ai-status.json", materialized)
            self.assertEqual(readme.read_text(encoding="utf-8"), "original owner readme content\n")
            self.assertTrue(os.path.islink(symlink))

    def test_materialize_worker_context_files_replaces_hardlink_without_mutating_tracked_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            status_root = tmp_p / "status_root"
            status_root.mkdir()
            canonical = b'{"project":"canonical"}\n'
            (status_root / "ai-status.json").write_bytes(canonical)

            wt_path = tmp_p / "worker"
            wt_path.mkdir()
            subprocess.run(["git", "init"], cwd=wt_path, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)
            readme = wt_path / "README.md"
            owner_bytes = b"tracked owner bytes\n"
            readme.write_bytes(owner_bytes)
            subprocess.run(["git", "add", "README.md"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "tracked baseline"], cwd=wt_path, capture_output=True, check=True)

            destination = wt_path / "ai-status.json"
            os.link(readme, destination)
            self.assertEqual(readme.stat().st_ino, destination.stat().st_ino)

            config = {
                "paths": {
                    "status_file": str(status_root / "ai-status.json"),
                    "activity_log": str(status_root / "ai-activity-log.jsonl"),
                },
            }
            request = supervisor.DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                task_id="TASK-HARDLINK-001",
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            materialized = supervisor.materialize_worker_context_files(config, request, wt_path)

            self.assertIn("ai-status.json", materialized)
            self.assertEqual(readme.read_bytes(), owner_bytes)
            self.assertEqual(destination.read_bytes(), canonical)
            self.assertNotEqual(readme.stat().st_ino, destination.stat().st_ino)
            diff = subprocess.run(
                ["git", "diff", "--exit-code", "--", "README.md"],
                cwd=wt_path,
                capture_output=True,
                check=False,
            )
            self.assertEqual(diff.returncode, 0, diff.stdout.decode(errors="replace"))

    def test_prepare_worker_workspace_recovers_dirty_worktree_lease_with_untracked_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_p = Path(tmpdir)
            remote_root = tmp_p / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_root)], capture_output=True, check=True)

            repo_root = tmp_p / "main_repo"
            subprocess.run(["git", "clone", str(remote_root), str(repo_root)], capture_output=True, check=True)
            (repo_root / "ai-status.json").write_text('{"project":"canonical"}', encoding="utf-8")
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "-b", "dev"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("main owner content\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", "dev"], cwd=repo_root, check=True)

            task_id = "TASK-B5-LEASE-001"
            branch_name = f"task/{task_id}"
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "push", "origin", branch_name], cwd=repo_root, check=True)
            subprocess.run(["git", "checkout", "dev"], cwd=repo_root, check=True)

            wt_path = tmp_p / "workers" / supervisor._task_id_slug(task_id)
            wt_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "worktree", "add", str(wt_path), branch_name], capture_output=True, check=True, cwd=repo_root)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=wt_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=wt_path, check=True)

            # Introduce an untracked symlink ai-status.json -> README.md into the reused worktree
            symlink = wt_path / "ai-status.json"
            symlink.symlink_to("README.md")

            config = {
                "paths": {
                    "status_file": str(repo_root / "ai-status.json"),
                    "activity_log": str(repo_root / "ai-activity-log.jsonl"),
                },
                "branch_workflow": {"task_branch_prefix": "task/", "dev_branch": "dev"},
                "worker_worktrees": {
                    "enabled": True,
                    "root": str(tmp_p / "workers"),
                    "base_ref": "origin/dev",
                    "reuse_existing": True,
                },
            }
            state: dict = {}
            request = supervisor.DeliveryRequest(
                agent_id="antigravity",
                provider="antigravity",
                delivery_mode="antigravity",
                message="wake",
                task_id=task_id,
                reason="owned_in_progress_dispatch",
                context_files=["ai-status.json"],
            )

            ok, message = supervisor.prepare_worker_workspace(
                config,
                state,
                request,
                queue_event_id="evt-b5-recover",
                target_agent="antigravity",
            )
            self.assertTrue(ok)
            self.assertIsNone(message)

            leased_path = Path(request.metadata["workspace_path"])
            # Fresh clean workspace allocated at a distinct path
            self.assertNotEqual(leased_path, wt_path)

            # Original dirty worktree at wt_path remains 100% byte-identical and untouched
            self.assertEqual((wt_path / "README.md").read_text(encoding="utf-8"), "main owner content\n")
            self.assertTrue(os.path.islink(wt_path / "ai-status.json"))

            # Fresh recovery workspace has regular materialized ai-status.json
            self.assertTrue(leased_path.exists())
            self.assertFalse(os.path.islink(leased_path / "ai-status.json"))
            self.assertEqual((leased_path / "ai-status.json").read_text(encoding="utf-8"), '{"project":"canonical"}')
class BlockedTaskRoleReassignmentTests(unittest.TestCase):
    """A blocked owner strands a task the same way a blocked reviewer does.

    Only the reviewer half was covered, so a task whose owner had run out of
    quota just waited: nothing reassigns an owner. Observed on
    ODP-ORCH-BRANCH-DRIFT-ALARMS-001, stuck 9 hours at review_approved.
    """

    def _config(self) -> dict:
        return {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {
                "helper_claim": {"enabled": True, "include_registered_idle_agents": True},
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
            },
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                },
                "claude": {"id": "claude", "display_name": "Claude", "provider": "claude"},
            },
            "providers": {},
        }

    def _state_with_codex_quota_paused(self) -> dict:
        return {
            "queue": {"events": {}},
            "workers": {},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2099-01-01T00:00:00Z",
                        "reason": "Codex usage limit reached",
                    }
                }
            },
        }

    def _run(self, status: dict) -> mock.Mock:
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(
                supervisor, "outstanding_delivery_indexes", return_value=(set(), set(), set())
            ),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            supervisor.reassign_blocked_task_roles_to_registered_idle_agents(
                self._config(), self._state_with_codex_quota_paused(), status
            )
        return persist

    def test_blocked_owner_at_finalize_is_reassigned(self) -> None:
        status = {
            "tasks": [
                {"id": "T-1", "status": "review_approved", "owner": "Codex", "reviewer": "Antigravity"}
            ]
        }

        persist = self._run(status)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["task_id"], "T-1")
        self.assertNotEqual(kwargs["new_owner"], "Codex")
        self.assertEqual(kwargs["new_reviewer"], "Antigravity")
        self.assertEqual(kwargs["handoff_from"], "Codex")

    def test_blocked_owner_in_progress_is_reassigned(self) -> None:
        status = {
            "tasks": [{"id": "T-2", "status": "in_progress", "owner": "Codex", "reviewer": "Antigravity"}]
        }

        persist = self._run(status)

        persist.assert_called_once()
        self.assertNotEqual(persist.call_args.kwargs["new_owner"], "Codex")

    def test_blocked_reviewer_at_review_still_reassigns_the_reviewer(self) -> None:
        status = {
            "tasks": [{"id": "T-3", "status": "review", "owner": "Antigravity", "reviewer": "Codex"}]
        }

        persist = self._run(status)

        persist.assert_called_once()
        kwargs = persist.call_args.kwargs
        self.assertEqual(kwargs["new_owner"], "Antigravity")
        self.assertNotEqual(kwargs["new_reviewer"], "Codex")
        self.assertEqual(kwargs["handoff_from"], "Codex")

    def test_healthy_owner_is_left_alone(self) -> None:
        status = {
            "tasks": [
                {"id": "T-4", "status": "review_approved", "owner": "Antigravity", "reviewer": "Claude"}
            ]
        }

        self._run(status).assert_not_called()

    def test_blocked_owner_at_review_is_not_reassigned(self) -> None:
        """At review the reviewer owes the work, so a blocked owner is not the blocker."""
        status = {
            "tasks": [{"id": "T-5", "status": "review", "owner": "Codex", "reviewer": "Antigravity"}]
        }

        self._run(status).assert_not_called()


if __name__ == "__main__":
    unittest.main()
