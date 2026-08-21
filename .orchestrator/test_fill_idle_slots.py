from __future__ import annotations

import os
import sys
import time
import unittest
import unittest.mock as mock
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dispatch_engine  # noqa: E402
import dispatch_policy  # noqa: E402
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

    def test_policy_does_not_materialize_legacy_cap_default(self) -> None:
        """Leaving max_dispatches_per_tick unset lets dispatch use slot total."""
        settings = dispatch_policy.ready_dispatch_settings({"ready_dispatcher": {}})

        self.assertNotIn("max_dispatches_per_tick", settings)

    def test_dead_stalled_workers_do_not_occupy_capacity(self) -> None:
        config = _config_with_slots(2)
        active_statuses = supervisor.active_worker_statuses(config)
        worker = {
            "status": "stalled",
            "pid": 99999999,
            "agent_id": "p_slot_1",
            "task_id": "TASK-1",
        }

        agents, task_agents = supervisor.active_worker_indexes(
            {"workers": {"run-1": worker}},
            active_statuses,
        )

        self.assertFalse(supervisor.worker_counts_as_active_capacity(config, worker, active_statuses))
        self.assertEqual(agents, set())
        self.assertEqual(task_agents, set())

    def test_recent_process_activity_keeps_quiet_worker_capacity_live(self) -> None:
        config = _config_with_slots(2)
        config["supervisor"] = {"stall_after_seconds": 300}
        active_statuses = supervisor.active_worker_statuses(config)
        now = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)
        worker = {
            "status": "stalled",
            "pid": os.getpid(),
            "last_event_at": "2026-08-21T10:00:00Z",
            "last_process_activity_at": "2026-08-21T10:29:30Z",
        }

        self.assertTrue(
            supervisor.worker_counts_as_active_capacity(
                config, worker, active_statuses, now=now
            )
        )

    def test_started_queue_record_is_not_double_counted_with_worker(self) -> None:
        config = _config_with_slots(2)
        active_statuses = supervisor.active_worker_statuses(config)
        event = {
            "event_id": "evt-1",
            "target_agent": "claude",
            "target_display_name": "Claude",
            "reason": "review_ready_dispatch",
        }
        state = {
            "workers": {
                "run-1": {
                    "status": "running",
                    "queue_event_id": "evt-1",
                    "request_snapshot": {"reason": "review_ready_dispatch"},
                    "logical_agent_id": "claude",
                    "agent_id": "p_slot_1",
                }
            },
            "queue": {"events": {"evt-1": {"status": "started"}}},
        }

        with mock.patch.object(supervisor, "load_event_queue", return_value=[event]):
            loads = dispatch_engine.agent_dispatch_loads(config, state, active_statuses)

        self.assertEqual(loads, {"claude": [0]})


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


class WakeSignalTests(unittest.TestCase):
    """Watching only the event queue missed the case that matters most.

    A worker finishing writes to the board, not to the queue, so on 2026-08-20
    a completion at 14:55:11 did not wake the loop and the next cycle ran at
    14:57:59 - the full 301s interval, with free slots the whole time.
    """

    def setUp(self) -> None:
        import tempfile

        self.dir = Path(tempfile.mkdtemp())
        self.queue = self.dir / "event-queue.jsonl"
        self.board = self.dir / "ai-status.json"
        self.queue.write_text("")
        self.board.write_text("{}")
        self.paths = {"event_queue": str(self.queue), "status_file": str(self.board)}
        self.config = {}

    def _patched(self):
        return mock.patch.object(
            supervisor, "config_path", side_effect=lambda _c, key: self.paths[key]
        )

    def test_the_board_is_a_wake_signal(self) -> None:
        with self._patched():
            def touch(_seconds):
                self.board.write_text('{"tasks": []}')

            with mock.patch.object(time, "sleep", side_effect=touch):
                woke = supervisor.sleep_until_work_or_interval(
                    self.config, 300.0, slice_seconds=0.01, min_wake_interval=0.0
                )

        self.assertTrue(woke)

    def test_the_queue_is_still_a_wake_signal(self) -> None:
        with self._patched():
            def touch(_seconds):
                self.queue.write_text('{"event_id": "e1"}\n')

            with mock.patch.object(time, "sleep", side_effect=touch):
                woke = supervisor.sleep_until_work_or_interval(
                    self.config, 300.0, slice_seconds=0.01, min_wake_interval=0.0
                )

        self.assertTrue(woke)

    def test_a_floor_stops_the_loop_chasing_its_own_fleet(self) -> None:
        """A running worker writes progress continuously; without a floor the
        supervisor would re-enter its cycle every few seconds."""
        with self._patched():
            def touch(_seconds):
                self.board.write_text('{"tasks": [1]}')

            with mock.patch.object(time, "sleep", side_effect=touch):
                woke = supervisor.sleep_until_work_or_interval(
                    self.config, 0.2, slice_seconds=0.01, min_wake_interval=1000.0
                )

        # The floor exceeds the interval, so the change is never acted on early.
        self.assertFalse(woke)

    def test_one_unreadable_signal_does_not_mask_the_other(self) -> None:
        self.queue.unlink()
        with self._patched():
            def touch(_seconds):
                self.board.write_text('{"tasks": [2]}')

            with mock.patch.object(time, "sleep", side_effect=touch):
                woke = supervisor.sleep_until_work_or_interval(
                    self.config, 300.0, slice_seconds=0.01, min_wake_interval=0.0
                )

        self.assertTrue(woke)


class ReconcileScopeTests(unittest.TestCase):
    """A task declaring another repository names branches that live there.

    Probing this checkout's `origin` for them reported drift that did not
    exist: on 2026-08-20 three `oday-data-platform` tasks were flagged as
    naming branches that "do not exist on the remote" while those branches
    were present in that repository.
    """

    def test_a_foreign_repository_task_is_left_alone(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "DPF-KRN-MEAS-001",
                    "status": "review_approved",
                    "branch": "task/DPF-KRN-MEAS-001",
                    "repository": "alfloop-dev/oday-data-platform",
                    "pr_number": 8,
                }
            ]
        }
        with mock.patch.object(
            dispatch_engine, "_this_repository_slug", return_value="alfloop-dev/odayplus"
        ), mock.patch.object(dispatch_engine, "_remote_branch_names") as branches:
            changed = dispatch_engine.reconcile_task_reality({}, status)

        self.assertFalse(changed)
        branches.assert_not_called()
        self.assertNotIn("next", status["tasks"][0])

    def test_a_task_for_this_repository_is_still_reconciled(self) -> None:
        status = {
            "tasks": [
                {
                    "id": "OPS-X-001",
                    "status": "in_progress",
                    "branch": "task/OPS-X-001",
                    "repository": "alfloop-dev/odayplus",
                }
            ]
        }
        with mock.patch.object(
            dispatch_engine, "_this_repository_slug", return_value="alfloop-dev/odayplus"
        ), mock.patch.object(
            dispatch_engine, "_remote_branch_names", return_value=set()
        ) as branches, mock.patch.object(
            dispatch_engine, "write_activity_log", create=True
        ), mock.patch.object(
            dispatch_engine, "commit_canonical_task_transition", create=True, return_value=True
        ), mock.patch.object(
            dispatch_engine, "_pull_request_record", return_value=None
        ):
            dispatch_engine.reconcile_task_reality({}, status)

        branches.assert_called_once()

    def test_an_unknown_local_repository_declines_declared_tasks(self) -> None:
        """Unable to tell whose repository this is, declining is the safe half."""
        status = {
            "tasks": [
                {
                    "id": "DPF-KRN-MEAS-001",
                    "status": "review_approved",
                    "branch": "task/DPF-KRN-MEAS-001",
                    "repository": "alfloop-dev/oday-data-platform",
                }
            ]
        }
        with mock.patch.object(
            dispatch_engine, "_this_repository_slug", return_value=None
        ), mock.patch.object(dispatch_engine, "_remote_branch_names") as branches:
            changed = dispatch_engine.reconcile_task_reality({}, status)

        self.assertFalse(changed)
        branches.assert_not_called()
