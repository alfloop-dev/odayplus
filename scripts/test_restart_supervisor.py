from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "restart-supervisor.sh"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


class RestartRefusalTests(unittest.TestCase):
    """Restarting from a tree nobody has reconciled is the failure this script
    exists to prevent, so it has to refuse rather than proceed."""

    def _repo_pair(self, tmp: Path) -> Path:
        origin = tmp / "origin.git"
        origin.mkdir()
        _git(origin, "init", "--bare", "--initial-branch=dev")

        work = tmp / "work"
        work.mkdir()
        _git(work, "init", "--initial-branch=dev")
        (work / "a.txt").write_text("one\n")
        _git(work, "add", "a.txt")
        _git(work, "commit", "-m", "one")
        _git(work, "remote", "add", "origin", str(origin))
        _git(work, "push", "-u", "origin", "dev")
        return work

    def _run(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env={**os.environ, "PANTHEON_ROOT": str(root), "RESTART_WAIT_SECONDS": "5"},
        )

    def test_a_diverged_tree_is_refused_and_nothing_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            work = self._repo_pair(tmp)

            # advance origin/dev, then commit something else locally so the
            # local branch can no longer fast-forward
            clone = tmp / "other"
            subprocess.run(
                ["git", "clone", str(tmp / "origin.git"), str(clone)],
                check=True, capture_output=True, text=True,
            )
            (clone / "b.txt").write_text("remote\n")
            _git(clone, "add", "b.txt")
            _git(clone, "commit", "-m", "remote change")
            _git(clone, "push", "origin", "dev")

            (work / "c.txt").write_text("local\n")
            _git(work, "add", "c.txt")
            _git(work, "commit", "-m", "local change")

            head_before = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()

            result = self._run(work)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to restart", result.stderr)
            head_after = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(head_before, head_after)

    def test_the_tree_is_advanced_before_anything_is_stopped(self) -> None:
        """Order is the whole point: stopping first gives the cron watchdog a
        60-second window to respawn from the old tree."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            work = self._repo_pair(tmp)

            clone = tmp / "other"
            subprocess.run(
                ["git", "clone", str(tmp / "origin.git"), str(clone)],
                check=True, capture_output=True, text=True,
            )
            (clone / "b.txt").write_text("remote\n")
            _git(clone, "add", "b.txt")
            _git(clone, "commit", "-m", "remote change")
            _git(clone, "push", "origin", "dev")

            (work / ".orchestrator").mkdir(parents=True, exist_ok=True)

            result = self._run(work)

            # No watchdog exists in the fixture, so it exits non-zero waiting
            # for a respawn - but only after the tree has already advanced.
            self.assertIn("no supervisor came back", result.stderr)
            merged = subprocess.run(
                ["git", "log", "--oneline"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn("remote change", merged)


if __name__ == "__main__":
    unittest.main()
