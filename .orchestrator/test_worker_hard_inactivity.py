#!/usr/bin/env python3
"""Pin the second stall tier: heartbeats are not evidence that work is happening.

`stall_after_seconds` compares `now` against `max(last_event_at,
last_process_activity_at)`, and `last_process_activity_at` advances whenever ANY
counter in the sampled process tree moves -- cpu_ticks, rchar, wchar,
read_bytes, write_bytes. That was chosen so a quiet-but-working CLI is not killed
mid-thought, and for a merely slow worker it is right.

It is wrong for a CLI that is blocked forever. On 2026-08-23 an antigravity
worker emitted a `python3 -c "..."` command whose body contained markdown
backtick fences; inside double quotes bash read them as command substitution,
forked a subshell, and blocked on a pipe that never closed. The CLI stayed up for
two hours and fifty-four minutes: twelve threads, ~0.4% CPU, three live TLS
sessions. cpu_ticks and rchar/wchar therefore kept moving every sample,
`last_process_activity_at` kept advancing, and the 300s stall timer never fired
even once. The workspace received zero writes across the whole window.

So storage I/O is the discriminator, and only over a long window. A rate
threshold does not work: a worker legitimately waiting on a model reply also
reports no CPU and no I/O, and would be killed by any instantaneous test. What
separates the two cases is duration -- minutes versus hours.

These tests state that contract so a future change cannot quietly fold
`last_disk_activity_at` back into the permissive signal.
"""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import supervisor
import worker_lifecycle
import worker_runner

TASK = {"id": "T-1", "status": "in_progress", "owner": "Claude", "reviewer": "Codex"}
STATUS = {"tasks": [TASK]}


def _ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _disk_subset(sample: dict[str, int]) -> dict[str, int]:
    """The projection the runner applies before deciding storage activity moved."""
    return {key: sample[key] for key in worker_runner.DISK_ACTIVITY_KEYS if key in sample}


class DiskActivitySubsetTests(unittest.TestCase):
    """The runner must not treat socket chatter as evidence of progress."""

    def test_disk_keys_exclude_cpu_and_character_io(self) -> None:
        # rchar/wchar count socket traffic too, so a blocked-but-connected CLI
        # keeps them moving. Only the block-layer counters go quiet.
        self.assertEqual(worker_runner.DISK_ACTIVITY_KEYS, ("read_bytes", "write_bytes"))
        for noisy in ("cpu_ticks", "rchar", "wchar", "processes"):
            self.assertNotIn(noisy, worker_runner.DISK_ACTIVITY_KEYS)

    def test_hung_process_sample_yields_an_unchanged_disk_subset(self) -> None:
        """The measured signature of the 2026-08-23 hang: counters move, disk does not."""
        before = {"processes": 2, "cpu_ticks": 4991, "rchar": 900, "wchar": 400,
                  "read_bytes": 8192, "write_bytes": 16384}
        after = {"processes": 2, "cpu_ticks": 4995, "rchar": 1400, "wchar": 700,
                 "read_bytes": 8192, "write_bytes": 16384}

        self.assertNotEqual(before, after, "the permissive signal would call this activity")
        self.assertEqual(_disk_subset(before), _disk_subset(after), "the disk signal must stay flat")

    def test_a_real_file_write_moves_the_disk_subset(self) -> None:
        before = {"cpu_ticks": 10, "read_bytes": 0, "write_bytes": 0}
        after = {"cpu_ticks": 10, "read_bytes": 0, "write_bytes": 4096}
        self.assertNotEqual(_disk_subset(before), _disk_subset(after))


class HardInactivityTerminationTests(unittest.TestCase):
    """poll_workers must reclaim a live worker that has written nothing for an hour."""

    def _worker(self, **overrides) -> dict:
        worker = {
            "run_id": "run-1",
            "status": "running",
            "task_id": "T-1",
            "provider": "antigravity",
            "agent_id": "antigravity",
            "target_agent": "Antigravity",
            "queue_event_id": "evt-1",
            "pid": 999999,
            "retry_count": 0,
            "attempt_count": 1,
            "reason": "task_in_progress",
            # Both permissive signals look healthy, exactly as they did during
            # the real hang -- the runner kept heart-beating throughout.
            "last_event_at": _ago(5),
            "last_process_activity_at": _ago(5),
            "last_heartbeat_at": _ago(5),
        }
        worker.update(overrides)
        return worker

    def _poll(self, worker: dict, *, hard_inactivity_seconds=None) -> str:
        state = {
            "workers": {"run-1": copy.deepcopy(worker)},
            "queue": {"events": {"evt-1": {"status": "started"}}},
            "provider_guardrails": {"dispatch_pauses": {}, "task_failure_streaks": {}},
        }
        config = supervisor.load_config(".orchestrator/config.json")
        if hard_inactivity_seconds is not None:
            config.setdefault("supervisor", {})["hard_inactivity_seconds"] = hard_inactivity_seconds
        # `poll_workers` is wrapped in `_entrypoint`, which copies
        # `supervisor.__dict__` into this module's globals on every call. Patching
        # `worker_lifecycle` directly is therefore overwritten before the body
        # runs; `supervisor` is the namespace that survives the sync.
        patches = [
            # The check only applies to a live worker; a dead one is settled by
            # the existing missing-process path instead.
            mock.patch.object(supervisor, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor, "terminate_worker_pid", return_value=True),
            mock.patch.object(supervisor, "load_status", return_value=STATUS),
            mock.patch.object(worker_lifecycle, "load_status", return_value=STATUS, create=True),
            mock.patch.object(supervisor, "worker_matches_current_assignment", return_value=True),
            mock.patch.object(supervisor, "higher_priority_ready_task_exists", return_value=False),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
            mock.patch.object(supervisor, "maybe_reassign_task_after_worker_failure", return_value=None),
        ]
        for patch in patches:
            patch.start()
        try:
            supervisor.poll_workers(config, state)
        finally:
            for patch in patches:
                patch.stop()
        return str(state["workers"].get("run-1", {}).get("status"))

    def test_worker_silent_on_disk_for_an_hour_is_terminated(self) -> None:
        worker = self._worker(last_disk_activity_at=_ago(3700))
        self.assertEqual(self._poll(worker, hard_inactivity_seconds=3600), "failed")

    def test_worker_writing_recently_survives(self) -> None:
        """A slow worker that still touches storage must not be reclaimed."""
        worker = self._worker(last_disk_activity_at=_ago(120))
        self.assertEqual(self._poll(worker, hard_inactivity_seconds=3600), "running")

    def test_quiet_worker_under_the_window_survives(self) -> None:
        """Waiting on a model reply is quiet for minutes; that is not a hang."""
        worker = self._worker(last_disk_activity_at=_ago(900))
        self.assertEqual(self._poll(worker, hard_inactivity_seconds=3600), "running")

    def test_zero_disables_the_tier(self) -> None:
        worker = self._worker(last_disk_activity_at=_ago(999999))
        self.assertEqual(self._poll(worker, hard_inactivity_seconds=0), "running")

    def test_worker_without_the_field_is_untouched(self) -> None:
        """Workers started before this change carry no signal; do not guess one."""
        worker = self._worker()
        worker.pop("last_disk_activity_at", None)
        self.assertEqual(self._poll(worker, hard_inactivity_seconds=3600), "running")


if __name__ == "__main__":
    unittest.main()
