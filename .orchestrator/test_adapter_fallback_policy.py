from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import common
import provider_permissions
from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest
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
                        "materialized_context_files": ["AI_COLLABORATION_GUIDE.md", "docs/source.md"],
                    },
                )

        self.assertEqual(env["AI_NAME"], "Codex")
        self.assertEqual(env["AI_STATUS_EXTRA_AGENTS"], "FleetAuditor,Codex")
        self.assertEqual(env["PANTHEON_WORKTREE_ROOT"], str(workspace))
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(status_root))
        self.assertEqual(env["ORCH_STATUS_ROOT"], str(status_root))
        self.assertEqual(
            json.loads(env["ORCH_MATERIALIZED_CONTEXT_PATHS"]),
            ["AI_COLLABORATION_GUIDE.md", "docs/source.md"],
        )

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

    def _protected_codex(self, root: Path, *, canonical: list[str] | None, prepared: list[str] | None):
        workspace = root / "worktree"
        workspace.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
        status_root = root / "status-root"
        status_root.mkdir()
        task = {"id": "GUARD-ADAPT-1"}
        if canonical is not None:
            task["protected_paths"] = canonical
        (status_root / "ai-status.json").write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
        config = {
            "paths": {"status_file": str(status_root / "ai-status.json")},
            "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"}},
            "providers": {"codex": {"codex": {"cli": "codex"}}},
        }
        metadata = {"workspace_path": str(workspace), "status_root": str(status_root)}
        if prepared is not None:
            metadata["protected_paths"] = prepared
        request = DeliveryRequest(
            agent_id="codex",
            provider="codex",
            delivery_mode="codex",
            message="wake",
            task_id="GUARD-ADAPT-1",
            metadata=metadata,
        )
        return CodexAdapter(config=config, provider_capabilities={}), request, workspace, status_root

    def test_protected_launch_passes_canonical_policy_to_the_shared_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter, request, workspace, status_root = self._protected_codex(
                Path(tmpdir), canonical=["guarded/zone"], prepared=["guarded/zone"]
            )
            fake_process = mock.Mock(pid=4321)
            with (
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, None)) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertEqual(spawn.call_args.kwargs["protected_paths"], ("guarded/zone",))
        self.assertEqual(spawn.call_args.kwargs["protected_alias_roots"], (status_root,))
        self.assertEqual(result.metadata["protected_execution"], "bwrap")

    def test_policy_drift_or_missing_sandbox_refuses_without_a_pid(self) -> None:
        for canonical, prepared in ((["guarded/zone"], None), (None, ["guarded/zone"]), (["guarded"], ["guarded/zone"])):
            with self.subTest(canonical=canonical, prepared=prepared), tempfile.TemporaryDirectory() as tmpdir:
                adapter, request, _workspace, _status = self._protected_codex(
                    Path(tmpdir), canonical=canonical, prepared=prepared
                )
                with (
                    mock.patch("adapters.codex.command_exists", return_value="codex"),
                    mock.patch("adapters.base.spawn_background_process") as spawn,
                ):
                    result = adapter.deliver(request)
                self.assertFalse(result.ok)
                self.assertIsNone(result.pid)
                self.assertIn("Protected execution refused", result.error or "")
                spawn.assert_not_called()
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter, request, _workspace, _status = self._protected_codex(
                Path(tmpdir), canonical=["guarded/zone"], prepared=["guarded/zone"]
            )
            with (
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch.object(common.worktree_cleanliness, "BWRAP_BINARY", str(Path(tmpdir) / "absent-bwrap")),
            ):
                result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertIsNone(result.pid)
        self.assertIn("not installed", result.error or "")

    def test_protected_task_never_degrades_to_the_shared_inbox_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_file = Path(tmpdir) / "ai-status.json"
            status_file.write_text(
                json.dumps({"tasks": [{"id": "GUARD-INBOX-1", "protected_paths": ["guarded/zone"]}]}),
                encoding="utf-8",
            )
            config = {"paths": {"status_file": str(status_file)}}
            capability = DeliveryCapability(
                adapter="antigravity",
                supported=False,
                requires_manual_confirmation=False,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="antigravity",
                verified="no",
                notes="CLI unavailable",
            )
            adapter = BaseAdapter(config=config, provider_capabilities={})
            for metadata in ({"protected_paths": ["guarded/zone"]}, {}):
                request = DeliveryRequest(
                    agent_id="agy",
                    provider="antigravity",
                    delivery_mode="antigravity",
                    message="wake",
                    task_id="GUARD-INBOX-1",
                    metadata=metadata,
                )
                with (
                    self.subTest(metadata=metadata),
                    mock.patch("adapters.file_inbox.FileInboxAdapter.deliver") as inbox,
                ):
                    result = adapter.unavailable_or_inbox(
                        request, capability, mode="antigravity", target="Agy", allow_inbox_fallback=True
                    )
                    self.assertFalse(result.ok)
                    self.assertEqual(result.mode, "antigravity")
                    inbox.assert_not_called()

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
            mock.patch("adapters.claude_cli.configured_provider_binary", return_value=None),
            mock.patch("adapters.claude_cli.claude_auth_ready", return_value=False),
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
                mock.patch("adapters.claude_cli.configured_provider_binary", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli.claude_auth_ready", return_value=True),
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
                mock.patch("adapters.claude_cli.configured_provider_binary", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli.claude_auth_ready", return_value=True),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, root / "claude.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-test-token")

    def test_claude_worker_uses_configured_model_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "allow_inbox_fallback": False,
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "model": "sonnet",
                            "effort": "medium",
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
                mock.patch("adapters.claude_cli.configured_provider_binary", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli.claude_auth_ready", return_value=True),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, root / "claude.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command[command.index("--effort") + 1], "medium")

    def test_claude_worker_omits_model_flags_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "allow_inbox_fallback": False,
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
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
                mock.patch("adapters.claude_cli.configured_provider_binary", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli.claude_auth_ready", return_value=True),
                mock.patch("adapters.base.spawn_background_process", return_value=(fake_process, root / "claude.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        command = spawn.call_args.args[0]
        self.assertNotIn("--model", command)
        self.assertNotIn("--effort", command)



class ClaudeAuthProbeRefreshTests(unittest.TestCase):
    """The dispatch probe must not log Claude out when only the access token lapsed."""

    def _write_credentials(self, home: Path, **oauth: object) -> Path:
        credentials_path = home / ".claude" / ".credentials.json"
        credentials_path.parent.mkdir(parents=True)
        credentials_path.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
        return credentials_path

    def _probe(self, home: Path) -> tuple[object, dict, mock.Mock, mock.Mock]:
        config = {
            "paths": {"claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json"},
            "providers": {
                "claude": {
                    "allow_inbox_fallback": False,
                    "runtime": {"cli": "claude", "home": str(home)},
                }
            }
        }
        status = mock.Mock(returncode=0, stdout=json.dumps({"loggedIn": True}))
        with (
            mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "", "CLAUDE_CONFIG_DIR": ""}, clear=False),
            mock.patch("adapters.claude_cli.configured_provider_binary", return_value="/usr/bin/claude"),
            mock.patch.object(provider_permissions, "configured_provider_binary", return_value="/usr/bin/claude"),
            mock.patch.object(
                provider_permissions,
                "cli_probe",
                return_value={"ok": True, "verdict": "ran", "returncode": 0, "error": None},
            ),
            mock.patch.object(common, "run_command", return_value=status),
            mock.patch.object(common.urllib.request, "urlopen") as urlopen,
            mock.patch.object(common, "write_json") as write_json,
        ):
            capability = ClaudeCLIAdapter(config=config, provider_capabilities={}).capability("claude")
            report = provider_permissions._claude_provider_report(
                config,
                provider_id="claude",
                claude_path=None,
                claude_version=None,
                claude_local={},
                claude_permissions={},
                workspace_settings={},
                claude_applied=False,
            )
        return capability, report, urlopen, write_json

    def test_expired_access_token_with_valid_refresh_token_is_dispatchable_offline(self) -> None:
        now_ms = int(common.time.time() * 1000)
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            credentials_path = self._write_credentials(
                home,
                accessToken="sk-ant-oat01-old",
                refreshToken="old-refresh",
                expiresAt=now_ms - 60_000,
                refreshTokenExpiresAt=now_ms + 86_400_000,
            )
            before = credentials_path.read_bytes()

            capability, report, urlopen, write_json = self._probe(home)

            self.assertTrue(capability.can_auto_deliver)
            self.assertEqual(capability.verified, "verified")
            self.assertTrue(report["auth_ready"])
            self.assertTrue(report["supports_auto_approve"])
            urlopen.assert_not_called()
            write_json.assert_not_called()
            self.assertEqual(credentials_path.read_bytes(), before)

    def test_expired_or_missing_refresh_token_is_not_authenticated(self) -> None:
        now_ms = int(common.time.time() * 1000)
        for refresh_fields in (
            {},
            {"refreshToken": "old-refresh", "refreshTokenExpiresAt": now_ms - 60_000},
        ):
            with self.subTest(refresh_fields=refresh_fields), tempfile.TemporaryDirectory() as tmpdir:
                home = Path(tmpdir)
                self._write_credentials(
                    home,
                    accessToken="sk-ant-oat01-old",
                    expiresAt=now_ms - 60_000,
                    **refresh_fields,
                )

                capability, report, urlopen, write_json = self._probe(home)

                self.assertFalse(capability.can_auto_deliver)
                self.assertEqual(
                    capability.notes,
                    "Claude CLI is installed but not authenticated; inbox fallback is disabled for this provider.",
                )
                self.assertFalse(report["auth_ready"])
                self.assertFalse(report["supports_auto_approve"])
                urlopen.assert_not_called()
                write_json.assert_not_called()

    def test_logged_in_status_without_stored_credentials_is_not_authenticated(self) -> None:
        for credentials in (None, {"mcpOAuth": {}}):
            with self.subTest(credentials=credentials), tempfile.TemporaryDirectory() as tmpdir:
                home = Path(tmpdir)
                credentials_path = home / ".claude" / ".credentials.json"
                if credentials is not None:
                    credentials_path.parent.mkdir(parents=True)
                    credentials_path.write_text(json.dumps(credentials), encoding="utf-8")

                capability, report, urlopen, write_json = self._probe(home)

                self.assertFalse(capability.can_auto_deliver)
                self.assertEqual(
                    capability.notes,
                    "Claude CLI is installed but not authenticated; inbox fallback is disabled for this provider.",
                )
                self.assertFalse(report["auth_ready"])
                self.assertFalse(report["supports_auto_approve"])
                urlopen.assert_not_called()
                write_json.assert_not_called()
                if credentials is None:
                    self.assertFalse(credentials_path.exists())


if __name__ == "__main__":
    unittest.main()
