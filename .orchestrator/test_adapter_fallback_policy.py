from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters.base import DeliveryRequest
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.codex import CodexAdapter
from common import delivery_runtime_env


class AdapterFallbackPolicyTests(unittest.TestCase):
    def test_delivery_runtime_env_registers_authorized_actor_and_preserves_existing_extras(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "task-worktree"
            status_root = root / "supervisor-root"
            with mock.patch.dict(
                os.environ,
                {"AI_STATUS_EXTRA_AGENTS": "FleetAuditor,Codex"},
                clear=False,
            ):
                env = delivery_runtime_env(
                    {"paths": {"status_file": str(status_root / "ai-status.json")}},
                    {
                        "workspace_path": str(workspace),
                        "status_root": str(status_root),
                        "target_display_name": "Codex",
                    },
                )

        self.assertEqual(env["AI_NAME"], "Codex")
        self.assertEqual(env["AI_STATUS_EXTRA_AGENTS"], "FleetAuditor,Codex")
        self.assertEqual(env["PANTHEON_WORKTREE_ROOT"], str(workspace))
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(status_root))
        self.assertEqual(env["ORCH_STATUS_ROOT"], str(status_root))

    def test_codex_alias_sets_agent_identity_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex2": {
                        "id": "codex2",
                        "display_name": "Codex2",
                        "provider": "codex2",
                        "adapter": "codex",
                    }
                },
                "providers": {
                    "codex2": {
                        "codex": {
                            "cli": "codex",
                            "api_key_env": "OPENAI_API_KEY_CODEX2",
                            "codex_home": "~/.codex2",
                        }
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="codex2",
                provider="codex2",
                delivery_mode="codex",
                message="wake",
                task_id="T-REVIEW",
                reason="review_ready_dispatch",
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY_CODEX2": "codex2-key",
                        "CODEX_THREAD_ID": "parent-thread",
                        "CODEX_SESSION_ID": "parent-session",
                    },
                    clear=False,
                ),
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, Path("/tmp/codex2.log"))) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["AI_NAME"], "Codex2")
        self.assertEqual(env["ORCH_AGENT_ID"], "codex2")
        self.assertEqual(env["ORCH_PROVIDER"], "codex2")
        self.assertEqual(env["ORCH_TASK_ID"], "T-REVIEW")
        self.assertEqual(env["ORCH_REASON"], "review_ready_dispatch")
        self.assertEqual(env["OPENAI_API_KEY"], "codex2-key")
        self.assertEqual(env["CODEX_HOME"], os.path.expanduser("~/.codex2"))
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("CODEX_SESSION_ID", env)

    def test_codex_model_setting_is_passed_to_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex2": {
                        "id": "codex2",
                        "display_name": "Codex2",
                        "provider": "codex2",
                        "adapter": "codex",
                    }
                },
                "providers": {
                    "codex2": {
                        "codex": {
                            "cli": "codex",
                            "model": "codex-5.3-spark",
                        }
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="codex2",
                provider="codex2",
                delivery_mode="codex",
                message="wake",
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch(
                    "adapters.base.spawn_background_process",
                    return_value=(fake_process, Path("/tmp/codex2.log")),
                ),
            ):
                result = adapter.deliver(request)

            self.assertTrue(result.ok)
            self.assertIn("--model", result.command)
            self.assertEqual(
                result.command[result.command.index("--model") + 1],
                "gpt-5.3-codex-spark",
            )

    def test_codex_without_api_key_env_does_not_inherit_parent_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex2": {
                        "id": "codex2",
                        "display_name": "Codex2",
                        "provider": "codex2",
                        "adapter": "codex",
                    }
                },
                "providers": {
                    "codex2": {
                        "codex": {
                            "cli": "codex",
                            "codex_home": "~/.codex2",
                        }
                    }
                },
            }
            request = DeliveryRequest(agent_id="codex2", provider="codex2", delivery_mode="codex", message="wake")
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "parent-key", "CODEX_THREAD_ID": "parent-thread"}, clear=False),
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, Path("/tmp/codex2.log"))) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["CODEX_HOME"], os.path.expanduser("~/.codex2"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_THREAD_ID", env)

    def test_codex_uses_request_workspace_and_status_root_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "worktree"
            status_root = root / "status-root"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"}},
                "providers": {"codex": {"codex": {"cli": "codex"}}},
            }
            request = DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                metadata={
                    "workspace_path": str(workspace),
                    "status_root": str(status_root),
                },
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, root / "codex.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.command[result.command.index("-C") + 1], str(workspace))
        self.assertEqual(spawn.call_args.kwargs["cwd"], workspace)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["PANTHEON_WORKTREE_ROOT"], str(workspace))
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(status_root))
        self.assertEqual(env["ORCH_STATUS_ROOT"], str(status_root))
        self.assertEqual(env["ORCH_WORKSPACE_PATH"], str(workspace))

    def test_claude_can_disable_inbox_fallback(self) -> None:
        config = {
            "providers": {
                "claude": {
                    "allow_inbox_fallback": False,
                    "runtime": {"cli": "claude"},
                }
            }
        }
        request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=None),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=False),
        ):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "claude_cli")

    def test_claude_alias_uses_provider_specific_home_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            gh_config = root / ".config" / "gh"
            gh_config.mkdir(parents=True)
            config = {
                "agents": {
                    "claude2": {
                        "id": "claude2",
                        "display_name": "Claude2",
                        "provider": "claude2",
                        "adapter": "claude_cli",
                    }
                },
                "paths": {
                    "status_file": "ai-status.json",
                    "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
                },
                "providers": {
                    "claude2": {
                        "allow_inbox_fallback": False,
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "home": "~/.claude2",
                            "output_format": "stream-json",
                            "include_hook_events": True,
                        },
                    }
                },
            }
            request = DeliveryRequest(agent_id="claude2", provider="claude2", delivery_mode="claude_cli", message="wake")
            adapter = ClaudeCLIAdapter(
                config=config,
                provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}},
            )
            fake_process = mock.Mock(pid=1234)
            with (
                mock.patch.dict(
                    os.environ,
                    {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"), "GH_CONFIG_DIR": ""},
                    clear=False,
                ),
                mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch(
                    "adapters.base.spawn_background_process",
                    return_value=(fake_process, Path("/tmp/claude2.log")),
                ) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["HOME"], str(root / ".claude2"))
        self.assertEqual(env["GH_CONFIG_DIR"], str(gh_config))
        self.assertEqual(env["ORCH_PROVIDER"], "claude2")
        self.assertIn("--permission-mode", result.command)

    def test_claude_runtime_loads_oauth_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "claude-token"
            token_file.write_text("sk-ant-oat01-test-token\n", encoding="utf-8")
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "allow_inbox_fallback": False,
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "oauth_token_file": str(token_file),
                            "output_format": "stream-json",
                            "include_hook_events": True,
                        },
                    }
                },
            }
            request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
            adapter = ClaudeCLIAdapter(config=config, provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, root / "claude.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-test-token")

if __name__ == "__main__":
    unittest.main()
