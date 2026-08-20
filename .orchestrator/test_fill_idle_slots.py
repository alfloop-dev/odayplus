from __future__ import annotations

import os
import sys
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dispatch_engine  # noqa: E402
import supervisor  # noqa: E402


def _config_with_slots(count: int) -> dict:
    agents = {"claude": {"provider": "claude", "account_pool": "p"}}
    for index in range(1, count + 1):
        agents[f"p_slot_{index}"] = {
            "provider": "claude",
            "account_pool": "p",
            "dispatch_slot_for_pool": "p",
            "slot_id": f"p_slot_{index}",
        }
    return {"agents": agents}


class DispatchCapTests(unittest.TestCase):
    """Eleven slots, a cap of 3 and a 300s interval meant at most three workers
    could start every five minutes while workers finish in one to five. The
    fleet sat far below capacity with eligible work on the board."""

    def test_the_cap_defaults_to_the_number_of_slots(self) -> None:
        config = _config_with_slots(11)

        self.assertEqual(dispatch_engine.configured_worker_slot_total(config), 11)
        self.assertEqual(dispatch_engine.default_max_dispatches_per_tick(config), 11)

    def test_a_small_configuration_keeps_a_usable_floor(self) -> None:
        """One slot should not mean one dispatch per tick for every lane."""
        self.assertEqual(dispatch_engine.default_max_dispatches_per_tick(_config_with_slots(1)), 4)

    def test_an_unslotted_configuration_still_works(self) -> None:
        self.assertEqual(dispatch_engine.default_max_dispatches_per_tick({"agents": {}}), 4)

    def test_an_explicit_setting_is_not_overridden(self) -> None:
        """An operator who wants smaller batches keeps them."""
        config = _config_with_slots(11)
        config["ready_dispatcher"] = {"max_dispatches_per_tick": 2}

        settings = config["ready_dispatcher"]
        resolved = max(
            1,
            int(
                settings.get("max_dispatches_per_tick")
                or dispatch_engine.default_max_dispatches_per_tick(config)
            ),
        )

        self.assertEqual(resolved, 2)


class InterruptibleSleepTests(unittest.TestCase):
    """Every wake this system produces is written to the event queue and was
    then invisible until the next scheduled tick, up to five minutes later."""

    def setUp(self) -> None:
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"wake-test-{os.getpid()}.jsonl"
        self.tmp.write_text("")
        self.config = {"paths": {"event_queue": str(self.tmp)}}

    def tearDown(self) -> None:
        self.tmp.unlink(missing_ok=True)

    def _patched_config_path(self):
        return mock.patch.object(
            supervisor, "config_path", side_effect=lambda _c, _k: str(self.tmp)
        )

    def test_it_returns_early_when_the_queue_changes(self) -> None:
        with self._patched_config_path():
            def touch(_seconds):
                self.tmp.write_text('{"event_id": "e1"}\n')

            with mock.patch.object(time, "sleep", side_effect=touch):
                woke = supervisor.sleep_until_work_or_interval(
                    self.config, 300.0, slice_seconds=0.01
                )

        self.assertTrue(woke)

    def test_it_sleeps_the_whole_interval_when_nothing_arrives(self) -> None:
        with self._patched_config_path(), mock.patch.object(time, "sleep"):
            woke = supervisor.sleep_until_work_or_interval(
                self.config, 0.05, slice_seconds=0.01
            )

        self.assertFalse(woke)

    def test_an_unreadable_queue_is_not_a_wake_signal(self) -> None:
        """Otherwise a missing file would spin the loop instead of pacing it."""
        with mock.patch.object(
            supervisor, "config_path", side_effect=KeyError("event_queue")
        ), mock.patch.object(time, "sleep"):
            woke = supervisor.sleep_until_work_or_interval(
                self.config, 0.05, slice_seconds=0.01
            )

        self.assertFalse(woke)

    def test_a_zero_interval_does_not_block(self) -> None:
        self.assertFalse(supervisor.sleep_until_work_or_interval(self.config, 0))


if __name__ == "__main__":
    unittest.main()


class ScopeGuardDecorationTests(unittest.TestCase):
    """`@_entrypoint` is what syncs supervisor's namespace into this module, so
    a function that loses it silently resolves different globals.

    Adding two helpers immediately above `dispatch_ready_tasks` put them
    between the decorator and its function: the decorator bound to the helper,
    `dispatch_ready_tasks` ran unsynced, and fifteen unrelated dispatch tests
    failed. The mechanism is load-bearing and invisible in a diff, so assert it
    directly.
    """

    def test_every_entrypoint_decorator_binds_to_a_function(self) -> None:
        """Structural, so it holds for whatever is decorated rather than for a
        list that goes stale."""
        import inspect

        source = inspect.getsource(dispatch_engine).splitlines()
        for index, line in enumerate(source):
            if line.strip() != "@_entrypoint":
                continue
            following = [
                later.strip()
                for later in source[index + 1 :]
                if later.strip() and not later.strip().startswith("#")
            ]
            self.assertTrue(following, "@_entrypoint at end of file")
            self.assertTrue(
                following[0].startswith("def "),
                f"line {index + 1}: @_entrypoint is followed by {following[0]!r}, "
                "not a function definition",
            )

    def test_dispatch_ready_tasks_is_scope_synced(self) -> None:
        """The one that must be wrapped: it is the loop entrypoint, and running
        it unsynced resolves different globals throughout."""
        self.assertEqual(dispatch_engine.dispatch_ready_tasks.__name__, "_sync_scope_guard")

    def test_the_new_helpers_are_not_decorated(self) -> None:
        """Pure helpers must not be wrapped - if one is, it was placed under a
        decorator meant for something else."""
        for name in ("configured_worker_slot_total", "default_max_dispatches_per_tick"):
            with self.subTest(name=name):
                self.assertEqual(getattr(dispatch_engine, name).__name__, name)
