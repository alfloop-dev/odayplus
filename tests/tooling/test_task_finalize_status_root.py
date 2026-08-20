from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / "delivery_toolchain" / "git" / "task_finalize.sh"
)


class StatusRootOverrideTests(unittest.TestCase):
    """`submit_review` writes to the canonical board, which lives at the fleet
    root. `ROOT` here is `git rev-parse --show-toplevel`, so inside a worker
    worktree it resolves to the worktree - where there is no board.

    Without an override the documented procedure is two steps: finalize with
    `--no-status-submit`, then re-run the submit with `PANTHEON_STATUS_ROOT`
    pointed back at the fleet root. The override collapses that to one.
    """

    def setUp(self) -> None:
        self.source = SCRIPT.read_text()

    def test_the_status_submit_honours_the_status_root_override(self) -> None:
        self.assertIn(
            '"${PANTHEON_STATUS_ROOT:-$ROOT}/scripts/ai-status.sh" submit_review',
            self.source,
        )

    def test_it_still_defaults_to_the_local_root(self) -> None:
        """The default must be unchanged, or every existing caller moves."""
        match = re.search(
            r'\$\{PANTHEON_STATUS_ROOT:-(\$[A-Za-z_]+)\}/scripts/ai-status\.sh',
            self.source,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "$ROOT")

    def test_no_bare_root_status_submit_remains(self) -> None:
        """A second unguarded call site would reintroduce the failure for
        whichever path reached it first."""
        bare = re.findall(r'"\$ROOT/scripts/ai-status\.sh"\s+submit_review', self.source)
        self.assertEqual(bare, [])


if __name__ == "__main__":
    unittest.main()
