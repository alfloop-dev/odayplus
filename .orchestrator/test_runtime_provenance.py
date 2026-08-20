from __future__ import annotations

import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supervisor  # noqa: E402


class RuntimeProvenanceTests(unittest.TestCase):
    """"Is the running supervisor the one with the fix?" must be answerable.

    On 2026-08-20 it was answered wrongly twice: once from a process start time
    that turned out to predate a fast-forward by 36 seconds, once from a
    `pgrep` that matched the observer's own command line. Both inferred from an
    artifact instead of asking the process what it loaded.
    """

    def setUp(self) -> None:
        supervisor._TREE_PROVENANCE_CACHE = None
        supervisor._LOADED_PROVENANCE = None

    def test_provenance_reports_the_loaded_commit_and_config(self) -> None:
        provenance = supervisor.runtime_provenance()

        self.assertIn("code_sha", provenance)
        self.assertIn("config_digest", provenance)
        if provenance["code_sha"]:
            self.assertRegex(provenance["code_sha"], r"^[0-9a-f]{40}$")

    def test_a_probe_that_does_not_return_an_object_name_is_discarded(self) -> None:
        """A mocked or failed `git rev-parse` must not be cached as provenance."""
        import subprocess

        with mock.patch.object(
            subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout="not-a-sha\n"),
        ):
            self.assertIsNone(supervisor.runtime_provenance()["code_sha"])

    def test_a_matching_runtime_is_not_stale(self) -> None:
        with mock.patch.object(
            supervisor,
            "runtime_provenance",
            return_value={"code_sha": "a" * 40, "config_digest": "cafe"},
        ):
            state = {"loaded_code_sha": "a" * 40, "loaded_config_digest": "cafe"}

            self.assertIsNone(supervisor.runtime_is_stale(state))

    def test_a_moved_checkout_is_reported(self) -> None:
        with mock.patch.object(
            supervisor,
            "runtime_provenance",
            return_value={"code_sha": "b" * 40, "config_digest": "cafe"},
        ):
            state = {"loaded_code_sha": "a" * 40, "loaded_config_digest": "cafe"}

            reason = supervisor.runtime_is_stale(state)

        self.assertIsNotNone(reason)
        self.assertIn("code moved from aaaaaaaa to bbbbbbbb", reason)
        self.assertIn("until it is restarted", reason)

    def test_a_changed_config_is_reported(self) -> None:
        """Config is read once at startup, so editing it changes nothing until
        a restart. Saying so is the difference between a setting that took
        effect and one that only looks as though it did."""
        with mock.patch.object(
            supervisor,
            "runtime_provenance",
            return_value={"code_sha": "a" * 40, "config_digest": "beef"},
        ):
            state = {"loaded_code_sha": "a" * 40, "loaded_config_digest": "cafe"}

            reason = supervisor.runtime_is_stale(state)

        self.assertIn("config document changed", reason)

    def test_an_unreadable_probe_is_not_drift(self) -> None:
        """A failed `git rev-parse` must not read as "the code moved"."""
        with mock.patch.object(
            supervisor,
            "runtime_provenance",
            return_value={"code_sha": None, "config_digest": None},
        ):
            state = {"loaded_code_sha": "a" * 40, "loaded_config_digest": "cafe"}

            self.assertIsNone(supervisor.runtime_is_stale(state))

    def test_a_process_that_never_stamped_is_not_drift(self) -> None:
        with mock.patch.object(
            supervisor,
            "runtime_provenance",
            return_value={"code_sha": "b" * 40, "config_digest": "beef"},
        ):
            self.assertIsNone(supervisor.runtime_is_stale({}))


if __name__ == "__main__":
    unittest.main()
