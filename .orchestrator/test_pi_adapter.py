from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = THIS_DIR.parent / "scripts"
for path in (THIS_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import worker_failure_policy
from adapters import build_adapter
from adapters.base import DeliveryRequest
from adapters.codex import CodexAdapter
from adapters.pi import PiAdapter
from common import ConfigError, validate_config


class PiAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.agent_dir = self.root / "pi-agent"
        self.agent_dir.mkdir()
        self.write_auth({"openai-codex": {"type": "oauth"}})

    def write_auth(self, credentials: object) -> None:
        (self.agent_dir / "auth.json").write_text(json.dumps(credentials), encoding="utf-8")

    def config(self, **pi_settings: str) -> dict:
        return {
            "paths": {"status_file": str(self.root / "ai-status.json")},
            "agents": {
                "pi1": {"id": "pi1", "display_name": "Pi1", "provider": "pi1", "adapter": "pi"},
            },
            "providers": {
                "pi1": {
                    "delivery_mode": "pi",
                    "pi": {"agent_dir": str(self.agent_dir), **pi_settings},
                },
            },
        }

    def deliver(self, config: dict, *, cli: str | None = "/usr/bin/pi", metadata: dict | None = None):
        request = DeliveryRequest(
            agent_id="pi1",
            provider="pi1",
            delivery_mode="pi",
            message="wake",
            task_id="T-PI",
            reason="owned_ready_dispatch",
            metadata=metadata or {},
        )
        fake_process = mock.Mock(pid=4321)
        with (
            mock.patch.dict(os.environ, {"PI_CODING_AGENT_SESSION_DIR": "/parent/sessions"}, clear=False),
            mock.patch("adapters.pi.command_exists", return_value=cli),
            mock.patch(
                "adapters.base.spawn_background_process",
                return_value=(fake_process, self.root / "pi.log"),
            ) as spawn,
        ):
            result = build_adapter("pi", config=config).deliver(request)
        return result, spawn

    def test_build_adapter_returns_pi_adapter(self) -> None:
        self.assertIsInstance(build_adapter("pi", config={}), PiAdapter)
        self.assertIsInstance(build_adapter("codex", config={}), CodexAdapter)

    def test_default_command_pins_openai_codex_and_its_default_model(self) -> None:
        result, spawn = self.deliver(self.config())

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.mode, "pi")
        self.assertEqual(
            spawn.call_args.args[0],
            ["/usr/bin/pi", "-p", "--provider", "openai-codex", "--model", "gpt-6.1-sol", "wake"],
        )
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["PI_CODING_AGENT_DIR"], str(self.agent_dir))
        self.assertNotIn("PI_CODING_AGENT_SESSION_DIR", env)
        self.assertEqual(env["AI_NAME"], "Pi1")
        self.assertEqual(env["ORCH_PROVIDER"], "pi1")
        self.assertEqual(env["ORCH_TASK_ID"], "T-PI")

    def test_model_thinking_and_cli_settings_reach_the_command(self) -> None:
        result, spawn = self.deliver(
            self.config(cli=".orchestrator/bin/pi", model="gpt-6.1-mini", thinking="High"),
            cli="/repo/.orchestrator/bin/pi",
        )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(
            spawn.call_args.args[0],
            [
                "/repo/.orchestrator/bin/pi",
                "-p",
                "--provider",
                "openai-codex",
                "--model",
                "gpt-6.1-mini",
                "--thinking",
                "high",
                "wake",
            ],
        )

    def test_agent_dir_is_expanded_into_the_env(self) -> None:
        config = self.config()
        config["providers"]["pi1"]["pi"]["agent_dir"] = "~/pi-agent"
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}, clear=False):
            result, spawn = self.deliver(config)

        self.assertTrue(result.ok, result.error)
        self.assertEqual(spawn.call_args.kwargs["env"]["PI_CODING_AGENT_DIR"], str(self.agent_dir))

    def test_invalid_thinking_fails_delivery_without_spawning(self) -> None:
        result, spawn = self.deliver(self.config(thinking="maximum"))

        self.assertFalse(result.ok)
        self.assertIn("Unsupported pi thinking 'maximum'", result.error or "")
        spawn.assert_not_called()

    def test_unknown_llm_provider_without_model_fails_delivery(self) -> None:
        self.write_auth({"anthropic": {"type": "oauth"}})
        result, spawn = self.deliver(self.config(llm_provider="anthropic"))

        self.assertFalse(result.ok)
        self.assertIn("set pi.model explicitly", result.error or "")
        spawn.assert_not_called()

    def test_missing_cli_is_unsupported(self) -> None:
        with mock.patch("adapters.pi.command_exists", return_value=None):
            capability = build_adapter("pi", config=self.config()).capability("pi1")

        self.assertFalse(capability.supported)
        self.assertEqual(capability.verified, "unavailable")
        self.assertIn("pi CLI is not installed.", capability.notes)
        self.assertIn("no sandbox or approval layer", capability.notes)

        result, spawn = self.deliver(self.config(), cli=None)
        self.assertFalse(result.ok)
        spawn.assert_not_called()

    def test_missing_auth_file_is_unsupported_with_login_hint(self) -> None:
        (self.agent_dir / "auth.json").unlink()
        with mock.patch("adapters.pi.command_exists", return_value="/usr/bin/pi"):
            capability = build_adapter("pi", config=self.config()).capability("pi1")

        self.assertFalse(capability.supported)
        self.assertIn("auth.json is missing", capability.notes)
        self.assertIn("/login", capability.notes)

    def test_auth_file_without_provider_credential_is_unsupported(self) -> None:
        # pi writes `{}` before anyone logs in.
        self.write_auth({})
        with mock.patch("adapters.pi.command_exists", return_value="/usr/bin/pi"):
            capability = build_adapter("pi", config=self.config()).capability("pi1")

        self.assertFalse(capability.supported)
        self.assertIn("has no openai-codex credential", capability.notes)

    def test_relative_agent_dir_is_rejected_even_when_supervisor_cwd_has_auth(self) -> None:
        # Supervisor cwd holds accounts/pi1/auth.json; the task worktree pi runs
        # in does not, so the relative dir must not pass the check here.
        supervisor_cwd = self.root / "supervisor"
        (supervisor_cwd / "accounts" / "pi1").mkdir(parents=True)
        (supervisor_cwd / "accounts" / "pi1" / "auth.json").write_text(
            json.dumps({"openai-codex": {"type": "oauth"}}), encoding="utf-8"
        )
        worktree = self.root / "worktree"
        worktree.mkdir()
        config = self.config(agent_dir="accounts/pi1")
        cwd = os.getcwd()
        os.chdir(supervisor_cwd)
        self.addCleanup(os.chdir, cwd)

        with mock.patch("adapters.pi.command_exists", return_value="/usr/bin/pi"):
            capability = build_adapter("pi", config=config).capability("pi1")
        self.assertFalse(capability.supported)
        self.assertIn("must be an absolute or ~-relative path", capability.notes)

        result, spawn = self.deliver(config, metadata={"workspace_path": str(worktree)})
        self.assertFalse(result.ok)
        spawn.assert_not_called()

    def test_ready_capability_states_the_missing_sandbox(self) -> None:
        with mock.patch("adapters.pi.command_exists", return_value="/usr/bin/pi"):
            capability = build_adapter("pi", config=self.config()).capability("pi1")

        self.assertTrue(capability.supported)
        self.assertEqual(capability.delivery_mode, "pi")
        self.assertIn("no sandbox or approval layer", capability.notes)


class PiConfigSchemaTests(unittest.TestCase):
    def validate(self, pi_settings: dict) -> None:
        validate_config(
            {"providers": {"pi1": {"delivery_mode": "pi", "pi": pi_settings}}},
            source="<test>",
        )

    def test_every_pi_setting_is_declared(self) -> None:
        self.validate(
            {
                "cli": ".orchestrator/bin/pi",
                "llm_provider": "openai-codex",
                "model": "gpt-6.1-sol",
                "thinking": "xhigh",
                "agent_dir": "~/.pi/agent-2",
            }
        )

    def test_unknown_thinking_level_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self.validate({"thinking": "maximum"})

    def test_relative_agent_dir_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self.validate({"agent_dir": "accounts/pi1"})


class PiFailureClassificationTests(unittest.TestCase):
    CONFIG = {"providers": {"pi1": {"delivery_mode": "pi"}}}

    def classify(self, reason: str) -> dict:
        return worker_failure_policy.classify_worker_failure(self.CONFIG, {"provider": "pi1"}, reason)

    def test_no_models_available_is_auth(self) -> None:
        reason = (
            "No models available. Use /login to log into a provider via OAuth or API key. See:\n"
            "  /home/u/.local/lib/node_modules/@earendil-works/pi-coding-agent/docs/providers.md"
        )
        self.assertEqual(self.classify(reason)["kind"], "auth")

    def test_no_api_key_found_is_auth(self) -> None:
        reason = "No API key found for openai-codex.\n\nUse /login to log into a provider via OAuth or API key."
        self.assertEqual(self.classify(reason)["kind"], "auth")

    def detect(self, text: str) -> str | None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "worker.log"
            log_path.write_text(text, encoding="utf-8")
            worker = {
                "run_id": "run-pi",
                "task_id": "T-PI",
                "provider": "pi1",
                "agent_id": "pi1",
                "status": "running",
                "runner_status": "failed",
                "exit_code": 1,
                "log_path": str(log_path),
                "pid": 999999,
            }
            return worker_failure_policy.detect_worker_failure(worker)

    def test_not_logged_in_stderr_is_detected_then_classified_as_auth(self) -> None:
        for text in (
            "No models available. Use /login to log into a provider via OAuth or API key. See:\n"
            "  /home/u/.local/lib/node_modules/@earendil-works/pi-coding-agent/docs/providers.md\n",
            "No API key found for openai-codex.\n\nUse /login to log into a provider via OAuth or API key.\n",
        ):
            with self.subTest(text=text.splitlines()[0]):
                reason = self.detect(text)
                self.assertIsNotNone(reason)
                self.assertEqual(self.classify(reason)["kind"], "auth")

    def test_quoted_not_logged_in_text_is_not_detected(self) -> None:
        for text in (
            'reason = "No API key found for openai-codex."\n',
            "adapters/pi.py:12: No models available. Use /login to log into a provider\n",
            "> No API key found for openai-codex.\n",
            "No API key found for openai-codex is the pi message we classify as auth.\n",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.detect(text))

    def test_pi_wrapper_missing_binary_is_provider_unavailable(self) -> None:
        reason = "pi CLI binary not found at /home/u/.local/bin/pi or on PATH."
        self.assertEqual(self.classify(reason)["kind"], "provider_unavailable")
        codex = worker_failure_policy.classify_worker_failure({}, {"provider": "codex"}, reason)
        self.assertNotEqual(codex["kind"], "provider_unavailable")


if __name__ == "__main__":
    unittest.main()
