#!/usr/bin/env python3
"""Contract tests for the launcher wrappers in `.orchestrator/bin/`.

`test_supervisor.test_detects_missing_cli_for_every_provider_wrapper` asserts that
the orchestrator RECOGNISES each wrapper's "binary not found" message, but it feeds
those messages in as string literals and never executes a wrapper. That test passed
for months while three of the five wrappers could not physically emit the string it
was checking: written as

    bin="$(ls -d <glob> 2>/dev/null | sort -V | tail -n1)"

under `set -euo pipefail`, a non-matching glob makes `ls` exit non-zero, `pipefail`
propagates it, and `set -e` aborts the script AT THE ASSIGNMENT -- before the
`echo ... >&2` that the contract depends on. The wrapper then exits 2 with empty
stdout AND stderr, `common.provider_launcher_missing_cli` finds nothing, and the
failure is classified `terminal` (keep dispatching) instead of `provider_unavailable`
(pause the lane).

These tests execute the real wrappers, so that regression cannot come back silently.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import common

BIN_DIR = Path(__file__).resolve().parent / "bin"

# Every wrapper, with the CLI name common.PROVIDER_LAUNCHER_MISSING_PATTERN must
# recover from its message when its target cannot be resolved.
WRAPPERS = {
    "agy": "antigravity",
    "claude": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "gh": "github",
}


def _run(name: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BIN_DIR / name), "--version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# Utilities the wrappers themselves need. PATH is rebuilt from just these so the
# `command -v <cli>` fallbacks in agy/codex find nothing, while the scripts can
# still run -- blanking PATH outright would break `#!/usr/bin/env bash` and test
# the harness instead of the wrapper.
_SHELL_UTILITIES = ("bash", "env", "getent", "id", "cut", "sort", "tail")


@contextlib.contextmanager
def _unresolvable_env():
    """An environment in which no wrapper can find its target."""
    with tempfile.TemporaryDirectory() as tmp:
        sandbox_bin = Path(tmp) / "bin"
        sandbox_bin.mkdir()
        for utility in _SHELL_UTILITIES:
            resolved = shutil.which(utility)
            if resolved:
                (sandbox_bin / utility).symlink_to(resolved)

        env = dict(os.environ)
        env["HOME"] = "/nonexistent-pantheon-home"
        env["PANTHEON_HOST_HOME"] = "/nonexistent-pantheon-home"
        env["PANTHEON_CLAUDE_EXTENSION_HOME"] = "/nonexistent-pantheon-home"
        env["AGY_BIN"] = ""
        env["PATH"] = str(sandbox_bin)
        yield env


class LauncherWrapperContractTests(unittest.TestCase):
    def test_every_wrapper_is_executable(self) -> None:
        for name in WRAPPERS:
            with self.subTest(wrapper=name):
                self.assertTrue(os.access(BIN_DIR / name, os.X_OK), f"{name} is not executable")

    def test_unresolvable_target_exits_nonzero(self) -> None:
        with _unresolvable_env() as env:
            for name in WRAPPERS:
                with self.subTest(wrapper=name):
                    self.assertNotEqual(_run(name, env).returncode, 0)

    def test_unresolvable_target_still_speaks(self) -> None:
        """The failure must never be silent -- this is what the shell bug broke."""
        with _unresolvable_env() as env:
            for name in WRAPPERS:
                with self.subTest(wrapper=name):
                    proc = _run(name, env)
                    self.assertTrue(
                        (proc.stderr or proc.stdout or "").strip(),
                        f"{name} failed with empty stdout AND stderr; "
                        "the orchestrator cannot classify a failure it cannot see",
                    )

    def test_message_is_recognised_as_a_missing_cli(self) -> None:
        """The emitted text must satisfy the contract the orchestrator matches on."""
        with _unresolvable_env() as env:
            for name, expected_cli in WRAPPERS.items():
                with self.subTest(wrapper=name):
                    proc = _run(name, env)
                    text = (proc.stderr or proc.stdout or "").strip()
                    detected = None
                    for line in text.splitlines():
                        detected = common.provider_launcher_missing_cli(line.strip())
                        if detected:
                            break
                    self.assertEqual(
                        detected,
                        expected_cli,
                        f"{name} emitted {text!r}, which "
                        "common.PROVIDER_LAUNCHER_MISSING_PATTERN does not recognise",
                    )

    def test_no_wrapper_uses_the_silent_ls_pipeline(self) -> None:
        """Guard the idiom itself, so a future edit cannot reintroduce the bug.

        `x="$(ls ... | sort | tail)"` is fatal under `set -euo pipefail`. Resolve
        globs with `shopt -s nullglob` + an array instead, as every wrapper now does.
        """
        for name in WRAPPERS:
            with self.subTest(wrapper=name):
                body = (BIN_DIR / name).read_text(encoding="utf-8")
                offenders = [
                    line.strip()
                    for line in body.splitlines()
                    if not line.strip().startswith("#")
                    and "=$(" in line
                    and "ls -d" in line
                    and "|" in line
                ]
                self.assertEqual(offenders, [], f"{name} resolves a glob through a pipeline")


if __name__ == "__main__":
    unittest.main()
