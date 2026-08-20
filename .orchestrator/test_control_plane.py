from __future__ import annotations

import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import control_plane  # noqa: E402


def _status(stdout: str, returncode: int = 0):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


class DirtyControlPlaneTests(unittest.TestCase):
    """The 2026-08-19 incident: a finalize worker blocked by the `done` gate
    edited that gate in the shared checkout. Uncommitted, in force, unreviewed,
    and unnoticed for roughly eighteen hours."""

    GLOBS = list(control_plane.DEFAULT_CONTROL_PLANE_GLOBS)

    def test_an_edited_governance_file_is_reported(self) -> None:
        runner = mock.Mock(return_value=_status(" M scripts/ai_status.py\n"))

        paths = control_plane.dirty_control_plane_paths(
            Path("/repo"), self.GLOBS, runner=runner
        )

        self.assertEqual(paths, ["scripts/ai_status.py"])
        alarm = control_plane.control_plane_alarm(paths)
        self.assertIn("without review", alarm)
        self.assertIn("scripts/ai_status.py", alarm)
        self.assertIn("no reviewer has seen it", alarm)

    def test_ordinary_dirty_files_are_not_the_control_plane(self) -> None:
        """The board and its derived views are dirty almost always."""
        runner = mock.Mock(
            return_value=_status(
                " M ai-status.json\n M dashboard-bundle.json\n M docs-site/current-work.md\n"
            )
        )

        paths = control_plane.dirty_control_plane_paths(
            Path("/repo"), self.GLOBS, runner=runner
        )

        self.assertEqual(paths, [])

    def test_a_rename_reports_the_destination(self) -> None:
        runner = mock.Mock(
            return_value=_status("R  old.py -> delivery_toolchain/git/check_commit_trailers.py\n")
        )

        paths = control_plane.dirty_control_plane_paths(
            Path("/repo"), self.GLOBS, runner=runner
        )

        self.assertEqual(paths, ["delivery_toolchain/git/check_commit_trailers.py"])

    def test_an_unreadable_status_is_not_clean(self) -> None:
        """A `git status` that cannot run is an unanswered question. Reporting
        it as clean is the same error as reconciling from a failed lookup."""
        runner = mock.Mock(return_value=_status("", returncode=128))

        self.assertIsNone(
            control_plane.dirty_control_plane_paths(Path("/repo"), self.GLOBS, runner=runner)
        )

        def raiser(*_a, **_k):
            raise OSError("git missing")

        self.assertIsNone(
            control_plane.dirty_control_plane_paths(Path("/repo"), self.GLOBS, runner=raiser)
        )

    def test_untracked_files_are_excluded(self) -> None:
        """An untracked file is not in force for anyone but its author; the
        governance question is about modified tracked code."""
        runner = mock.Mock(return_value=_status(""))

        control_plane.dirty_control_plane_paths(Path("/repo"), self.GLOBS, runner=runner)

        args = runner.call_args.args[0]
        self.assertIn("--untracked-files=no", args)


class SettingsTests(unittest.TestCase):
    def test_it_is_on_and_reporting_by_default(self) -> None:
        settings = control_plane.control_plane_settings({})

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["mode"], "report")
        self.assertIn("scripts/ai_status.py", settings["globs"])

    def test_blocking_is_available_but_not_the_default(self) -> None:
        """A halt on a dirty control plane would have stopped the fleet
        repeatedly during ordinary work, and a control that has to be disabled
        to get anything done is a control that gets disabled."""
        settings = control_plane.control_plane_settings(
            {"control_plane_guard": {"mode": "block"}}
        )

        self.assertEqual(settings["mode"], "block")

    def test_operator_globs_replace_the_defaults(self) -> None:
        settings = control_plane.control_plane_settings(
            {"control_plane_guard": {"globs": ["only/this.py"]}}
        )

        self.assertEqual(settings["globs"], ["only/this.py"])


if __name__ == "__main__":
    unittest.main()


class ObserverSafetyTests(unittest.TestCase):
    """An observer must never be able to halt what it observes."""

    def test_a_failed_activity_log_does_not_take_down_dispatch(self) -> None:
        import supervisor

        with mock.patch.object(
            supervisor, "check_control_plane", wraps=supervisor.check_control_plane
        ), mock.patch.object(
            control_plane,
            "dirty_control_plane_paths",
            return_value=["scripts/ai_status.py"],
        ), mock.patch.object(
            supervisor, "write_activity_log", side_effect=KeyError("activity_log")
        ), mock.patch.object(supervisor, "console_log") as console:
            blocked = supervisor.check_control_plane({}, {})

        self.assertFalse(blocked)
        printed = " ".join(str(call) for call in console.call_args_list)
        self.assertIn("without review", printed)
        self.assertIn("could not be written", printed)

    def test_block_mode_stops_dispatch(self) -> None:
        import supervisor

        with mock.patch.object(
            control_plane,
            "dirty_control_plane_paths",
            return_value=["scripts/ai_status.py"],
        ), mock.patch.object(supervisor, "write_activity_log"), mock.patch.object(
            supervisor, "console_log"
        ):
            blocked = supervisor.check_control_plane(
                {"control_plane_guard": {"mode": "block"}}, {}
            )

        self.assertTrue(blocked)

    def test_a_clean_tree_clears_a_previous_alarm(self) -> None:
        import supervisor

        state = {"supervisor": {"control_plane_dirty": ["x"], "control_plane_reported": "old"}}
        with mock.patch.object(
            control_plane, "dirty_control_plane_paths", return_value=[]
        ):
            blocked = supervisor.check_control_plane({}, state)

        self.assertFalse(blocked)
        self.assertNotIn("control_plane_dirty", state["supervisor"])
        self.assertNotIn("control_plane_reported", state["supervisor"])
