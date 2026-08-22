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


class RestartRetirementTests(unittest.TestCase):
    """The retired shortcut must never mutate a developer checkout."""

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

    def test_a_diverged_tree_is_refused_without_touching_head(self) -> None:
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
            self.assertIn("is retired", result.stderr)
            self.assertIn("will not fetch, merge, reset", result.stderr)
            head_after = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(head_before, head_after)

    def test_remote_advance_is_not_merged_by_the_retired_shortcut(self) -> None:
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

            before = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()

            result = self._run(work)

            self.assertEqual(result.returncode, 2)
            self.assertIn("rollout_supervisor_runtime.py", result.stderr)
            after = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
