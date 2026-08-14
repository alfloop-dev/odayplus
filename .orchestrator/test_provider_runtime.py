from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import provider_runtime


class ProviderRuntimeTests(unittest.TestCase):
    def test_provider_config_entry_resolves_hyphenated_alias_once(self) -> None:
        config = {"providers": {"codex1-1": {"delivery_mode": "codex"}}}
        key, settings = provider_runtime.provider_config_entry(config, "Codex1_1")
        self.assertEqual(key, "codex1-1")
        self.assertEqual(settings["delivery_mode"], "codex")

    def test_explicit_lookup_fails_closed_and_adapter_can_request_default(self) -> None:
        config = {"providers": {"claude": {"delivery_mode": "claude_cli"}}}
        self.assertEqual(provider_runtime.provider_config(config, "missing"), {})
        self.assertEqual(
            provider_runtime.provider_config(config, "missing", default="claude"),
            {"delivery_mode": "claude_cli"},
        )

    def test_provider_key_uses_agent_provider_and_canonical_config_key(self) -> None:
        config = {
            "agents": {"slot_one": {"provider": "codex1_1"}},
            "providers": {"codex1-1": {"delivery_mode": "codex"}},
        }
        self.assertEqual(
            provider_runtime.provider_key(config, default="codex", agent_id="slot-one"),
            "codex1-1",
        )

    def test_claude_probe_and_worker_environment_share_one_builder(self) -> None:
        config = {"providers": {"claude-alt": {"runtime": {
            "home": "~/claude-alt", "env": {"CLAUDE_PROFILE": "alternate"}
        }}}}
        with (
            mock.patch.dict(os.environ, {"GH_TOKEN": "token"}, clear=True),
            mock.patch.object(provider_runtime, "apply_claude_oauth_token_file") as apply_token,
        ):
            env = provider_runtime.claude_runtime_env(config, "claude_alt")
        self.assertEqual(env["HOME"], os.path.expanduser("~/claude-alt"))
        self.assertEqual(env["CLAUDE_PROFILE"], "alternate")
        self.assertEqual(env["GH_TOKEN"], "token")
        apply_token.assert_called_once()

    def test_codex_config_health_uses_nested_profile_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            (home / "config.toml").write_text('service_tier = "priority"\n', encoding="utf-8")
            config = {"providers": {"codex-alt": {
                "delivery_mode": "codex", "codex": {"codex_home": str(home)}
            }}}
            result = provider_runtime.codex_config_health(config, "codex_alt")
        self.assertFalse(result["valid"])
        self.assertIn("unsupported service_tier", result["error"])


if __name__ == "__main__":
    unittest.main()
