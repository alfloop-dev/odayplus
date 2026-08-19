#!/usr/bin/env python3
"""Tests for the provider CLI login helpers.

The Antigravity flow is covered end to end against a stub `agy` that reproduces
the real CLI's behaviour -- the login menu, an OAuth URL hard-wrapped inside a
bordered box, and the paste-the-code prompt -- so the helper's menu handling,
URL reassembly, code hand-off and token wait are all exercised without touching
Google.
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import antigravity_login_helper
import codex_login_helper
import common
from pty_login import unwrap_boxed_text

# Shaped like the real Antigravity consent URL: long, percent-encoded, and with
# `state` last -- the parameter the helper uses to detect a complete frame.
OAUTH_URL = (
    "https://accounts.google.com/o/oauth2/auth?access_type=offline"
    "&client_id=1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    "&code_challenge=D1MJ9rrEzSSm2nsaJZTp9wvYcY4XtOkC2_yi9VwZjt0&code_challenge_method=S256"
    "&prompt=consent&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
    "&response_type=code&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fcloud-platform"
    "+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email+openid"
    "&state=ShgfgjmlQymQwwvm4vwmuA"
)
EXPECTED_CODE = "4/0AVGtestauthcode"

FAKE_AGY = '''#!/usr/bin/env python3
"""Stub of the Antigravity CLI onboarding TUI."""
import os
import sys
import time
from pathlib import Path

URL = "%(url)s"
EXPECTED_CODE = "%(code)s"
WIDTH = 138


def draw(width=WIDTH):
    rule = " " + "\\u2500" * width
    sys.stdout.write(" Open the URL below in your browser:\\n")
    sys.stdout.write(rule + "\\n")
    # The real CLI hard-wraps the URL and pads each line out to the box width.
    for start in range(0, len(URL), width):
        sys.stdout.write(" " + URL[start:start + width].ljust(width) + "\\n")
    sys.stdout.write(rule + "\\n")
    sys.stdout.flush()


sys.stdout.write("\\n Welcome to the Antigravity CLI. You are currently not signed in.\\n\\n")
sys.stdout.write(" Select login method:\\n > 1. Google OAuth\\n   2. Use a Google Cloud project\\n")
sys.stdout.write(" [Use arrow keys to navigate, Enter to select]\\n")
sys.stdout.flush()

# Wait for the helper to accept the preselected "Google OAuth" entry.
sys.stdin.readline()

# A truncated redraw lands first, exactly as the spinner does in the real CLI.
sys.stdout.write(" Open the URL below in your browser:\\n")
sys.stdout.write(" " + URL[:90] + "\\n")
sys.stdout.flush()
draw()

sys.stdout.write("\\n After authenticating, copy the code displayed in the browser and paste it below:\\n")
sys.stdout.write("\\n authorization code...\\n")
sys.stdout.flush()

code = sys.stdin.readline().strip()
if code == EXPECTED_CODE:
    token = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text('{"access_token": "stub", "code": "' + code + '"}', encoding="utf-8")
    sys.stdout.write("\\n Signed in.\\n")
else:
    sys.stdout.write("\\n Invalid authorization code.\\n")
sys.stdout.flush()

# The real CLI drops into its session rather than exiting.
time.sleep(30)
'''


def _write_fake_agy(directory: Path) -> Path:
    script = directory / "fake-agy"
    script.write_text(FAKE_AGY % {"url": OAUTH_URL, "code": EXPECTED_CODE}, encoding="utf-8")
    script.chmod(0o755)
    return script


def _boxed_region(url: str, width: int = 138) -> str:
    rule = " " + "─" * width
    lines = [rule]
    lines += [" " + url[i : i + width].ljust(width) for i in range(0, len(url), width)]
    lines.append(rule)
    return "\n".join(lines)


class UnwrapTests(unittest.TestCase):
    def test_unwrap_rejoins_hard_wrapped_url(self) -> None:
        self.assertEqual(unwrap_boxed_text(_boxed_region(OAUTH_URL)), OAUTH_URL)

    def test_extract_recovers_url_from_boxed_frame(self) -> None:
        buffer = f"{antigravity_login_helper.URL_MARKER}:\n{_boxed_region(OAUTH_URL)}\n\nAfter authenticating, ..."
        self.assertEqual(antigravity_login_helper.extract_oauth_url(buffer), OAUTH_URL)

    def test_extract_skips_frame_missing_state_parameter(self) -> None:
        # A spinner redraw can emit a truncated URL; accepting it would send the
        # operator to a broken consent page.
        truncated = f"{antigravity_login_helper.URL_MARKER}:\n {OAUTH_URL[:90]}\n"
        self.assertIsNone(antigravity_login_helper.extract_oauth_url(truncated))

    def test_extract_returns_none_without_marker(self) -> None:
        self.assertIsNone(antigravity_login_helper.extract_oauth_url("nothing here"))


class AntigravityLoginFlowTests(unittest.TestCase):
    """Drive helper.main() against the stub CLI over a real pty."""

    def _run(self, extra_args: list[str], *, home: Path, cli: Path) -> int:
        config = {
            "providers": {
                "antigravity": {
                    "antigravity": {"cli": str(cli), "config_home": str(home), "home": str(home)}
                }
            }
        }
        with (
            mock.patch.object(antigravity_login_helper, "load_config", return_value=config),
            mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
        ):
            return antigravity_login_helper.main(["--provider", "antigravity", *extra_args])

    def test_full_flow_writes_token_for_correct_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            home = tmpdir / "home"
            home.mkdir()
            code = self._run(
                ["--auth-code", EXPECTED_CODE, "--timeout", "20"],
                home=home,
                cli=_write_fake_agy(tmpdir),
            )

            token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            self.assertEqual(code, 0)
            self.assertTrue(token.exists())
            # Proves the code survived the pty round trip unmangled.
            self.assertIn(EXPECTED_CODE, token.read_text(encoding="utf-8"))

    def test_auth_code_file_is_picked_up_once_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            home = tmpdir / "home"
            home.mkdir()
            code_file = tmpdir / "code.txt"
            # The operator drops the code in only after the URL has been shown.
            timer = threading.Timer(1.5, lambda: code_file.write_text(EXPECTED_CODE, encoding="utf-8"))
            timer.start()
            try:
                code = self._run(
                    ["--auth-code-file", str(code_file), "--code-wait", "25", "--timeout", "20"],
                    home=home,
                    cli=_write_fake_agy(tmpdir),
                )
            finally:
                timer.cancel()

            self.assertEqual(code, 0)
            self.assertTrue((home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists())

    def test_rejected_code_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            home = tmpdir / "home"
            home.mkdir()
            code = self._run(
                ["--auth-code", "wrong-code", "--timeout", "5"],
                home=home,
                cli=_write_fake_agy(tmpdir),
            )

            self.assertEqual(code, 5)
            self.assertFalse((home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists())

    def test_status_only_tracks_token_presence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            home = tmpdir / "home"
            home.mkdir()
            cli = _write_fake_agy(tmpdir)

            self.assertEqual(self._run(["--status-only"], home=home, cli=cli), 1)

            token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text("{}", encoding="utf-8")

            self.assertEqual(self._run(["--status-only"], home=home, cli=cli), 0)

    def test_existing_token_short_circuits_without_launching_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            home = tmpdir / "home"
            token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text("{}", encoding="utf-8")

            # /bin/false stands in for the CLI: reaching it at all would fail.
            self.assertEqual(self._run([], home=home, cli=Path("/bin/false")), 0)


class CodexHelperTests(unittest.TestCase):
    SAMPLE_OUTPUT = textwrap.dedent(
        """
        Welcome to Codex [v0.147.0]

        1. Open this link in your browser and sign in to your account
           https://auth.openai.com/codex/device

        2. Enter this one-time code (expires in 15 minutes)
           X0N6-U0GWB
        """
    )

    def test_device_url_and_code_are_extracted(self) -> None:
        url = codex_login_helper.DEVICE_URL_PATTERN.search(self.SAMPLE_OUTPUT)
        code = codex_login_helper.DEVICE_CODE_PATTERN.search(self.SAMPLE_OUTPUT)
        self.assertIsNotNone(url)
        self.assertIsNotNone(code)
        self.assertEqual(url.group(0), "https://auth.openai.com/codex/device")
        self.assertEqual(code.group(1), "X0N6-U0GWB")

    def test_build_env_sets_codex_home_and_drops_inherited_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-inherited"}):
                env, resolved = codex_login_helper.build_env({}, str(codex_home))

            self.assertEqual(resolved, str(codex_home))
            self.assertEqual(env["CODEX_HOME"], str(codex_home))
            self.assertTrue(codex_home.is_dir())
            # An inherited key would make Codex skip the ChatGPT sign-in entirely.
            self.assertNotIn("OPENAI_API_KEY", env)

    def test_build_env_keeps_api_key_when_provider_declares_one(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-inherited"}):
            env, resolved = codex_login_helper.build_env({"api_key_env": "OPENAI_API_KEY"}, None)

        self.assertIsNone(resolved)
        self.assertEqual(env["OPENAI_API_KEY"], "sk-inherited")


class CommandExistsTests(unittest.TestCase):
    def test_repo_relative_path_resolves_independently_of_cwd(self) -> None:
        relative = ".orchestrator/bin/agy"
        self.assertTrue((common.ROOT / relative).is_file(), "fixture shim missing")

        original = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            resolved = common.command_exists(relative)
        finally:
            os.chdir(original)

        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isabs(resolved))
        self.assertEqual(resolved, str(common.ROOT / relative))

    def test_bare_name_resolves_on_path_to_absolute(self) -> None:
        resolved = common.command_exists("sh")
        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isabs(resolved))

    def test_missing_and_empty_values_return_none(self) -> None:
        self.assertIsNone(common.command_exists("definitely-not-a-real-binary-xyz"))
        self.assertIsNone(common.command_exists(""))
        self.assertIsNone(common.command_exists(None))


if __name__ == "__main__":
    unittest.main()
