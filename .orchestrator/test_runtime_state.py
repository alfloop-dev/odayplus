#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
import runtime_state
import supervisor


class LoadRuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _queue_event(self, task_id: str) -> dict:
        return {
            "task_id": task_id,
            "target_agent": "codex",
            "provider": "codex",
            "reason": "test",
            "message": f"Work on {task_id}",
        }

    def test_enqueue_event_owns_envelope_defaults_and_storage(self) -> None:
        event = self._queue_event("QUEUE-001")

        payload = runtime_state.enqueue_event(self.config, event)

        self.assertNotIn("event_id", event)
        self.assertTrue(payload["event_id"].startswith("evt-"))
        self.assertTrue(payload["created_at"])
        self.assertEqual(payload["context_files"], [])
        self.assertEqual(payload["target_files"], [])
        self.assertEqual(payload["metadata"], {})
        self.assertEqual(
            runtime_state.load_event_queue(self.config),
            [payload],
        )

    def test_enqueue_event_rejects_invalid_envelope_before_writing(self) -> None:
        event = self._queue_event("QUEUE-002")
        event.pop("target_agent")

        with self.assertRaisesRegex(ValueError, "target_agent"):
            runtime_state.enqueue_event(self.config, event)

        self.assertEqual(runtime_state.load_event_queue(self.config), [])

    def test_replace_event_queue_preserves_events_appended_after_snapshot(self) -> None:
        first = runtime_state.enqueue_event(self.config, self._queue_event("QUEUE-OLD"))
        original = runtime_state.load_event_queue(self.config)
        second = runtime_state.enqueue_event(self.config, self._queue_event("QUEUE-NEW"))

        runtime_state.replace_event_queue(
            self.config,
            original_events=original,
            retained_events=[],
        )

        self.assertEqual(runtime_state.load_event_queue(self.config), [second])
        self.assertNotEqual(first["event_id"], second["event_id"])

    def test_load_runtime_state_drops_suspended_worker_without_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-stale": {
                        "run_id": "claude-stale",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-missing",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["workers"], {})

    def test_load_runtime_state_keeps_suspended_worker_with_live_queue_event(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-live": {
                        "run_id": "claude-live",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "suspended_approval",
                        "queue_event_id": "evt-live",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-live", "task_id": "EXEC-FRONT-TW03-001"}) + "\n",
            encoding="utf-8",
        )

        state = runtime_state.load_runtime_state(self.config)

        self.assertIn("claude-live", state["workers"])

    def test_save_does_not_resurrect_reaped_manual_pending_worker(self) -> None:
        """A stale disk snapshot must not revive a worker trimmed in memory."""
        self._write_json(
            self.root / "state.json",
            {
                "workers": {
                    "claude-stale": {
                        "run_id": "claude-stale",
                        "task_id": "EXEC-FRONT-TW03-001",
                        "status": "manual_pending",
                        "queue_event_id": "evt-missing",
                    }
                },
                "queue": {"events": {}},
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.default_state()
        runtime_state.save_runtime_state(self.config, state)

        self.assertNotIn("claude-stale", state["workers"])
        self.assertNotIn(
            "claude-stale", runtime_state.load_runtime_state(self.config)["workers"]
        )

    def test_load_runtime_state_drops_retired_chair_scheduler_state(self) -> None:
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertNotIn("chair_rotation", state)
        self.assertNotIn("chair_review", state["supervisor"]["mode_occupancy"])

    def test_load_runtime_state_preserves_watchdog_safe_mode(self) -> None:
        self._write_json(
            self.root / "state.json",
            {
                "workers": {},
                "queue": {"events": {}},
                "watchdog": {
                    "safe_mode_until": "2026-05-18T14:30:00Z",
                    "safe_mode_reason": "stale_heartbeat",
                },
            },
        )
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["watchdog"]["safe_mode_until"], "2026-05-18T14:30:00Z")
        self.assertEqual(state["watchdog"]["safe_mode_reason"], "stale_heartbeat")
        self.assertIn("last_safe_mode_observed_until", state["watchdog"])

    def test_ready_dispatch_cursor_survives_save_and_reload(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        state = runtime_state.default_state()
        state["ready_dispatcher"]["dispatch_cursor"] = 73
        state["ready_dispatcher"]["dispatch_cursor_revision"] = 19
        state["ready_dispatcher"]["dispatch_cursor_updated_at"] = "2026-07-31T12:00:00Z"

        runtime_state.save_runtime_state(self.config, state)
        reloaded = runtime_state.load_runtime_state(self.config)

        self.assertEqual(reloaded["ready_dispatcher"]["dispatch_cursor"], 73)
        self.assertEqual(reloaded["ready_dispatcher"]["dispatch_cursor_revision"], 19)
        self.assertEqual(
            reloaded["ready_dispatcher"]["dispatch_cursor_updated_at"],
            "2026-07-31T12:00:00Z",
        )

    def test_ready_dispatch_cursor_migrates_legacy_state_and_fails_safe(self) -> None:
        malformed_values = (
            None,
            [],
            "front",
            {"weighted_cursor": "invalid"},
            {"weighted_cursor": -9},
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed):
                migrated = runtime_state.migrate_state({"ready_dispatcher": malformed})
                self.assertEqual(migrated["ready_dispatcher"]["dispatch_cursor"], 0)

        for malformed_revision in (None, True, [], "invalid", -9):
            with self.subTest(malformed_revision=malformed_revision):
                migrated = runtime_state.migrate_state(
                    {
                        "ready_dispatcher": {
                            "weighted_cursor": 7,
                            "weighted_cursor_revision": malformed_revision,
                        }
                    }
                )
                self.assertEqual(
                    migrated["ready_dispatcher"]["dispatch_cursor_revision"],
                    0,
                )

        migrated = runtime_state.migrate_state(
            {
                "ready_dispatcher": {
                    "weighted_cursor": 7,
                    "weighted_cursor_revision": 4,
                    "weighted_cursor_updated_at": "not-a-timestamp",
                }
            }
        )
        self.assertIsNone(
            migrated["ready_dispatcher"]["dispatch_cursor_updated_at"]
        )

    def test_newer_cursor_revision_wins_with_equal_wall_clock_timestamp(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "dispatch_cursor": 17,
            "dispatch_cursor_revision": 41,
            "dispatch_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        newer_state = runtime_state.default_state()
        newer_state["ready_dispatcher"] = {
            "dispatch_cursor": 18,
            "dispatch_cursor_revision": 42,
            "dispatch_cursor_updated_at": "2026-07-31T12:00:00Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, newer_state)

        self.assertEqual(merged["ready_dispatcher"]["dispatch_cursor"], 18)
        self.assertEqual(
            merged["ready_dispatcher"]["dispatch_cursor_revision"],
            42,
        )

    def test_concurrent_auxiliary_save_cannot_roll_back_weighted_cursor(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "dispatch_cursor": 17,
            "dispatch_cursor_revision": 42,
            "dispatch_cursor_updated_at": "2026-07-31T11:59:00Z",
        }
        stale_claim_state = runtime_state.default_state()
        stale_claim_state["ready_dispatcher"] = {
            "dispatch_cursor": 3,
            "dispatch_cursor_revision": 41,
            "dispatch_cursor_updated_at": "2099-12-31T23:59:59Z",
        }
        stale_claim_state["workers"]["antigravity7-live"] = {
            "run_id": "antigravity7-live",
            "status": "running",
        }
        stale_claim_state["queue"]["events"]["evt-live"] = {
            "event_id": "evt-live",
            "status": "started",
            "run_id": "antigravity7-live",
        }

        merged = runtime_state.merge_runtime_states(disk_state, stale_claim_state)

        self.assertEqual(merged["ready_dispatcher"]["dispatch_cursor"], 17)
        self.assertEqual(
            merged["ready_dispatcher"]["dispatch_cursor_revision"],
            42,
        )
        self.assertIn("antigravity7-live", merged["workers"])
        self.assertIn("evt-live", merged["queue"]["events"])

    def test_stale_auxiliary_save_composes_worker_and_queue_with_disk_cursor(self) -> None:
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-live", "task_id": "TASK-LIVE"}) + "\n",
            encoding="utf-8",
        )
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "dispatch_cursor": 17,
            "dispatch_cursor_revision": 42,
            "dispatch_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        runtime_state.save_runtime_state(self.config, disk_state)

        stale_claim_state = runtime_state.default_state()
        stale_claim_state["ready_dispatcher"] = {
            "dispatch_cursor": 3,
            "dispatch_cursor_revision": 41,
            "dispatch_cursor_updated_at": "2099-12-31T23:59:59Z",
        }
        stale_claim_state["workers"]["antigravity7-live"] = {
            "run_id": "antigravity7-live",
            "status": "running",
        }
        stale_claim_state["queue"]["events"]["evt-live"] = {
            "event_id": "evt-live",
            "status": "started",
            "run_id": "antigravity7-live",
        }

        runtime_state.save_runtime_state(self.config, stale_claim_state)
        reloaded = runtime_state.load_runtime_state(self.config)

        self.assertEqual(reloaded["ready_dispatcher"]["dispatch_cursor"], 17)
        self.assertEqual(
            reloaded["ready_dispatcher"]["dispatch_cursor_revision"],
            42,
        )
        self.assertIn("antigravity7-live", reloaded["workers"])
        self.assertIn("evt-live", reloaded["queue"]["events"])

    def test_save_does_not_resurrect_queue_record_pruned_from_canonical_queue(self) -> None:
        (self.root / "event-queue.jsonl").write_text(
            json.dumps({"event_id": "evt-live", "task_id": "TASK-LIVE"}) + "\n",
            encoding="utf-8",
        )
        disk_state = runtime_state.default_state()
        disk_state["queue"]["events"] = {
            "evt-live": {"status": "queued", "attempt_count": 0},
            "evt-pruned": {
                "status": "started",
                "attempt_count": 1,
                "lease_owner": "run-pruned",
            },
        }
        self._write_json(self.root / "state.json", disk_state)

        stale_writer = runtime_state.default_state()
        stale_writer["queue"]["events"]["evt-live"] = {
            "status": "queued",
            "attempt_count": 0,
        }
        runtime_state.save_runtime_state(self.config, stale_writer)

        persisted = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(set(persisted["queue"]["events"]), {"evt-live"})
        self.assertNotIn("evt-pruned", stale_writer["queue"]["events"])

    def test_save_preserves_concurrently_added_canonical_queue_record(self) -> None:
        (self.root / "event-queue.jsonl").write_text(
            "".join(
                [
                    json.dumps({"event_id": "evt-original", "task_id": "TASK-ORIGINAL"}) + "\n",
                    json.dumps({"event_id": "evt-concurrent", "task_id": "TASK-CONCURRENT"}) + "\n",
                ]
            ),
            encoding="utf-8",
        )
        disk_state = runtime_state.default_state()
        disk_state["queue"]["events"]["evt-concurrent"] = {
            "status": "started",
            "attempt_count": 1,
            "lease_owner": "run-concurrent",
        }
        self._write_json(self.root / "state.json", disk_state)

        stale_writer = runtime_state.default_state()
        stale_writer["queue"]["events"]["evt-original"] = {
            "status": "queued",
            "attempt_count": 0,
        }
        runtime_state.save_runtime_state(self.config, stale_writer)

        persisted = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(persisted["queue"]["events"]),
            {"evt-original", "evt-concurrent"},
        )
        self.assertEqual(
            persisted["queue"]["events"]["evt-concurrent"]["status"],
            "started",
        )

    def test_save_does_not_resurrect_trimmed_terminal_worker_history(self) -> None:
        self.config["supervisor"] = {"max_worker_history": 2}
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        disk_state = runtime_state.default_state()
        for index in range(5):
            disk_state["workers"][f"run-{index}"] = {
                "run_id": f"run-{index}",
                "status": "completed",
                "last_event_at": f"2026-08-08T10:0{index}:00Z",
            }
        self._write_json(self.root / "state.json", disk_state)

        singleton_state = runtime_state.default_state()
        singleton_state["workers"] = {
            "run-3": disk_state["workers"]["run-3"],
            "run-4": disk_state["workers"]["run-4"],
        }

        runtime_state.save_runtime_state(self.config, singleton_state)
        persisted = json.loads((self.root / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(set(persisted["workers"]), {"run-3", "run-4"})
        self.assertEqual(set(singleton_state["workers"]), {"run-3", "run-4"})

    def test_save_retains_concurrent_active_worker_while_compacting_history(self) -> None:
        self.config["supervisor"] = {"max_worker_history": 2}
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        disk_state = runtime_state.default_state()
        disk_state["workers"] = {
            "run-old": {
                "run_id": "run-old",
                "status": "completed",
                "last_event_at": "2026-08-08T10:00:00Z",
            },
            "run-concurrent": {
                "run_id": "run-concurrent",
                "status": "running",
                "last_event_at": "2026-08-08T10:01:00Z",
            },
        }
        self._write_json(self.root / "state.json", disk_state)

        stale_writer = runtime_state.default_state()
        stale_writer["workers"]["run-new"] = {
            "run_id": "run-new",
            "status": "completed",
            "last_event_at": "2026-08-08T10:02:00Z",
        }

        runtime_state.save_runtime_state(self.config, stale_writer)
        persisted = json.loads((self.root / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(set(persisted["workers"]), {"run-concurrent", "run-new"})
        self.assertEqual(persisted["workers"]["run-concurrent"]["status"], "running")

    def test_equal_cursor_revision_prefers_durable_disk_snapshot(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "dispatch_cursor": 17,
            "dispatch_cursor_revision": 42,
            "dispatch_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        auxiliary_state = runtime_state.default_state()
        auxiliary_state["ready_dispatcher"] = {
            "dispatch_cursor": 3,
            "dispatch_cursor_revision": 42,
            "dispatch_cursor_updated_at": "2099-12-31T23:59:59Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, auxiliary_state)

        self.assertEqual(merged["ready_dispatcher"]["dispatch_cursor"], 17)
        self.assertEqual(
            merged["ready_dispatcher"]["dispatch_cursor_revision"],
            42,
        )

    def test_malformed_revision_and_future_timestamp_cannot_pin_cursor(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "dispatch_cursor": 17,
            "dispatch_cursor_revision": "not-a-revision",
            "dispatch_cursor_updated_at": "2099-12-31T23:59:59Z",
        }
        valid_state = runtime_state.default_state()
        valid_state["ready_dispatcher"] = {
            "dispatch_cursor": 18,
            "dispatch_cursor_revision": 1,
            "dispatch_cursor_updated_at": "2026-07-31T12:00:00Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, valid_state)

        self.assertEqual(merged["ready_dispatcher"]["dispatch_cursor"], 18)
        self.assertEqual(
            merged["ready_dispatcher"]["dispatch_cursor_revision"],
            1,
        )


class QueueCustodyRuleTests(unittest.TestCase):
    """The one queue custody rule, over what the real writers leave on a record."""

    OLD = {"run_id": "run-old", "queue_event_id": "evt-1", "status": "running", "attempt_count": 1}
    FRESH = {"run_id": "run-new", "queue_event_id": "evt-1", "status": "running", "attempt_count": 2}

    def _moved(self, record: dict, worker: dict, *others: dict) -> bool:
        workers = {run["run_id"]: run for run in (worker, *others)}
        state = {"workers": workers, "queue": {"events": {"evt-1": record}}}
        return runtime_state.queue_event_moved_from_worker(state, worker)

    def _bound(self, run_id: str = "run-old") -> dict:
        return {
            "status": "started",
            "attempt_count": 1,
            "last_attempt_at": "2026-11-05T00:00:00Z",
            "run_id": run_id,
            "lease_owner": run_id,
            "processed_at": "2026-11-05T00:00:00Z",
        }

    def _reset(self, record: dict) -> dict:
        runtime_state._reset_queue_record_for_redispatch(record, reason="redispatch")
        return record

    def test_bound_run_holds_and_every_other_run_does_not(self) -> None:
        self.assertFalse(self._moved(self._bound(), self.OLD))
        self.assertTrue(self._moved(self._bound("run-9"), self.OLD))
        self.assertTrue(self._moved({**self._bound(), "lease_owner": "run-9"}, self.OLD))
        self.assertTrue(self._moved({**self._bound(), "run_id": "run-9"}, self.OLD))

    def test_event_without_a_record_or_a_run_that_names_none_is_not_moved(self) -> None:
        self.assertFalse(self._moved(self._bound(), {"run_id": "x"}))
        state = {"workers": {}, "queue": {"events": {}}}
        self.assertFalse(runtime_state.queue_event_moved_from_worker(state, self.OLD))

    def test_reset_takes_the_event_from_every_run_that_names_it(self) -> None:
        record = self._reset(self._bound())
        self.assertEqual(record["status"], "queued")
        for dropped in ("run_id", "lease_owner", "processed_at", "last_attempt_at"):
            self.assertNotIn(dropped, record)
        self.assertTrue(self._moved(record, self.OLD))
        self.assertTrue(self._moved(record, self.FRESH))

    def test_reset_persisted_by_an_older_supervisor_still_holds(self) -> None:
        record = {
            "status": "queued",
            "attempt_count": 1,
            "run_id": "run-old",
            "last_attempt_at": "2026-11-05T00:00:00Z",
            "requeued_at": "2026-11-05T00:00:01Z",
        }
        self.assertTrue(self._moved(record, self.OLD))

    def _attempted_after_reset(self) -> dict:
        record = self._reset(self._bound())
        record.update(attempt_count=2, last_attempt_at=record["requeued_at"])
        return record

    def test_launch_between_its_save_and_its_binding_holds_and_the_old_run_does_not(self) -> None:
        never_reset = {"status": "started", "attempt_count": 1, "last_attempt_at": "2026-11-05T00:00:00Z"}
        self.assertFalse(self._moved(never_reset, self.OLD))
        record = self._attempted_after_reset()
        self.assertFalse(self._moved(record, self.FRESH))
        self.assertTrue(self._moved(record, self.OLD))

    def test_retry_child_is_not_mistaken_for_the_new_attempt(self) -> None:
        # A child counts its own attempts, so it can land on the new attempt's number.
        child = {**self.FRESH, "run_id": "run-child", "parent_run_id": "run-old"}
        self.assertTrue(self._moved(self._attempted_after_reset(), child, self.OLD))

    def test_lineage_holds_across_generations_in_both_directions(self) -> None:
        parent = {**self.OLD, "status": "retried", "superseded_by_run_id": "run-child"}
        child = {**self.FRESH, "run_id": "run-child", "parent_run_id": "run-old"}
        record = self._bound()
        self.assertFalse(self._moved(record, child, parent))
        record["lease_owner"] = "run-child"
        self.assertFalse(self._moved(record, child, parent))
        self.assertFalse(self._moved(record, parent, child))
        # The first run may be gone from history; its child still names it.
        self.assertFalse(self._moved(record, child))
        self.assertTrue(self._moved(self._reset(record), child, parent))

    def test_run_on_another_event_does_not_extend_the_lineage(self) -> None:
        other = {"run_id": "other", "queue_event_id": "evt-2", "parent_run_id": "run-old"}
        child = {**self.FRESH, "run_id": "run-child", "parent_run_id": "other"}
        self.assertTrue(self._moved(self._bound(), child, other, self.OLD))

    def test_cycle_in_run_links_terminates(self) -> None:
        one = {"run_id": "one", "queue_event_id": "evt-1", "parent_run_id": "two"}
        two = {"run_id": "two", "queue_event_id": "evt-1", "parent_run_id": "one"}
        self.assertTrue(self._moved(self._bound("run-9"), one, two))


class QueueCustodyPersistenceTests(unittest.TestCase):
    """Reset and moved events through the real load, rebuild, merge and save."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        root = Path(self.tmpdir.name)
        self.config = {"paths": {"state_file": str(root / "state.json"), "event_queue": str(root / "queue.jsonl")}}
        runtime_state.enqueue_event(
            self.config,
            {"event_id": "evt-1", "target_agent": "claude", "provider": "claude", "reason": "test", "message": "go"},
        )

    def _worker(self, status: str, run_id: str = "run-old", **fields: object) -> dict:
        return {"run_id": run_id, "queue_event_id": "evt-1", "status": status, "attempt_count": 1, **fields}

    def _bound_state(self, status: str = "running") -> dict:
        return {
            "workers": {"run-old": self._worker(status)},
            "queue": {
                "events": {
                    "evt-1": {
                        "status": "started",
                        "attempt_count": 1,
                        "last_attempt_at": "2026-11-05T00:00:00Z",
                        "run_id": "run-old",
                        "lease_owner": "run-old",
                        "processed_at": "2026-11-05T00:00:00Z",
                    }
                }
            },
        }

    def _record(self) -> dict:
        return runtime_state.load_runtime_state(self.config)["queue"]["events"]["evt-1"]

    def test_reset_survives_reload_whatever_the_old_run_became(self) -> None:
        for status in ("running", "retry_backoff", "waiting_approval", "manual_pending", "failed", "completed", "retried"):
            with self.subTest(status):
                state = self._bound_state(status)
                runtime_state.save_runtime_state(self.config, state)
                runtime_state._reset_queue_record_for_redispatch(state["queue"]["events"]["evt-1"], reason="redispatch")
                runtime_state.save_runtime_state(self.config, state)
                self.assertEqual(state["queue"]["events"]["evt-1"]["status"], "queued")
                record = self._record()
                self.assertEqual(record["status"], "queued")
                self.assertNotIn("lease_owner", record)
                self.assertNotIn("run_id", record)
                self.assertEqual(self._record(), record, "reload must be stable")
                Path(self.config["paths"]["state_file"]).unlink()

    def test_stale_writer_cannot_undo_a_reset_on_disk(self) -> None:
        stale = self._bound_state()
        runtime_state.save_runtime_state(self.config, deepcopy(stale))
        fresh = runtime_state.load_runtime_state(self.config)
        runtime_state._reset_queue_record_for_redispatch(fresh["queue"]["events"]["evt-1"], reason="redispatch")
        runtime_state.save_runtime_state(self.config, fresh)
        runtime_state.save_runtime_state(self.config, stale)
        self.assertEqual(stale["queue"]["events"]["evt-1"]["status"], "queued")
        record = self._record()
        self.assertEqual(record["status"], "queued")
        self.assertNotIn("lease_owner", record)

    def test_merge_takes_the_newer_reset_whole_from_either_side(self) -> None:
        bound = self._bound_state()["queue"]["events"]["evt-1"]
        reset = deepcopy(bound)
        runtime_state._reset_queue_record_for_redispatch(reset, reason="redispatch")
        self.assertEqual(runtime_state._merge_queue_record(bound, reset), reset)
        self.assertEqual(runtime_state._merge_queue_record(reset, bound), reset)

    def test_merge_keeps_a_later_transition_over_a_stale_reset(self) -> None:
        reset = {"status": "queued", "requeued_at": "2026-11-05T00:00:05Z"}
        rebound = {"status": "started", "run_id": "run-new", "lease_owner": "run-new", "processed_at": "2026-11-05T00:00:09Z"}
        self.assertEqual(runtime_state._merge_queue_record(rebound, reset)["lease_owner"], "run-new")
        self.assertEqual(runtime_state._merge_queue_record(reset, rebound)["lease_owner"], "run-new")
        # The same reset carried forward by both sides is ranked as before.
        attempt = {**reset, "status": "started", "run_id": "run-new", "lease_owner": "run-new", "last_attempt_at": "2026-11-05T00:00:06Z"}
        self.assertEqual(runtime_state._merge_queue_record(attempt, reset)["lease_owner"], "run-new")
        self.assertEqual(runtime_state._merge_queue_record(reset, attempt)["lease_owner"], "run-new")

    def test_merge_without_a_reset_keeps_the_furthest_transition(self) -> None:
        started = {"status": "started", "lease_owner": "run-new"}
        self.assertEqual(runtime_state._merge_queue_record(started, {"status": "queued"})["status"], "started")
        self.assertEqual(runtime_state._merge_queue_record({"status": "completed"}, started)["status"], "completed")
        self.assertEqual(runtime_state._merge_queue_record(started, {"status": "failed"})["status"], "failed")

    def test_event_moved_to_another_run_is_decided_by_that_run_alone(self) -> None:
        state = self._bound_state("failed")
        state["workers"]["run-new"] = self._worker("running", "run-new", attempt_count=2)
        state["queue"]["events"]["evt-1"].update(run_id="run-new", lease_owner="run-new")
        runtime_state.save_runtime_state(self.config, state)
        record = self._record()
        self.assertEqual((record["status"], record["lease_owner"]), ("started", "run-new"))
        self.assertNotIn("error", record)

        state["workers"]["run-new"]["status"] = "completed"
        runtime_state.save_runtime_state(self.config, state)
        self.assertEqual(self._record()["status"], "completed")

    def test_ordinary_rebuild_outcomes_are_preserved(self) -> None:
        for status, expected in (
            ("running", "started"),
            ("waiting_approval", "manual_pending"),
            ("failed", "failed"),
            ("completed", "completed"),
        ):
            with self.subTest(status):
                state = self._bound_state(status)
                state["workers"]["run-old"]["last_error"] = "boom"
                runtime_state.save_runtime_state(self.config, state)
                self.assertEqual(self._record()["status"], expected)
                Path(self.config["paths"]["state_file"]).unlink()

    def test_launch_saved_before_its_binding_is_rebuilt_as_started(self) -> None:
        state = {
            "workers": {"run-new": self._worker("running", "run-new")},
            "queue": {"events": {"evt-1": {"status": "queued", "attempt_count": 1, "last_attempt_at": "2026-11-05T00:00:00Z"}}},
        }
        runtime_state.save_runtime_state(self.config, state)
        self.assertEqual(self._record()["status"], "started")


class TopLevelStateKeyPersistenceTests(unittest.TestCase):
    """`migrate_state` must never discard a top-level key without being told to.

    The old filter kept only keys already in `default_state()` plus a hardcoded
    whitelist. That made "a writer added a state key and forgot to declare it"
    indistinguishable from "the feature works": every save appeared to succeed,
    every read returned the default, and nothing was ever logged. The
    worktree-lease escalation counter lived in exactly that blind spot and so
    never fired once.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
            }
        }
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

    def test_undeclared_top_level_keys_survive_migration(self) -> None:
        migrated = runtime_state.migrate_state(
            {
                "some_future_counter": {"task-a": {"count": 4}},
                "another_new_key": [1, 2, 3],
            }
        )

        self.assertEqual(migrated["some_future_counter"], {"task-a": {"count": 4}})
        self.assertEqual(migrated["another_new_key"], [1, 2, 3])

    def test_undeclared_top_level_keys_survive_save_and_reload(self) -> None:
        state = runtime_state.default_state()
        state["some_future_counter"] = {"task-a": {"count": 4}}

        runtime_state.save_runtime_state(self.config, state)

        # `save_runtime_state` rewrites the caller's live dict from what it
        # persisted, so a dropped key is lost in memory too -- which is why a
        # counter incremented once per loop could never climb past 1.
        self.assertEqual(state["some_future_counter"], {"task-a": {"count": 4}})
        reloaded = runtime_state.load_runtime_state(self.config)
        self.assertEqual(reloaded["some_future_counter"], {"task-a": {"count": 4}})

    def test_only_explicitly_retired_keys_are_dropped(self) -> None:
        with mock.patch.object(
            runtime_state, "RETIRED_STATE_KEYS", frozenset({"legacy_bucket"})
        ):
            migrated = runtime_state.migrate_state(
                {"legacy_bucket": {"stale": True}, "kept_bucket": {"live": True}}
            )

        self.assertNotIn("legacy_bucket", migrated)
        self.assertEqual(migrated["kept_bucket"], {"live": True})

    def test_worktree_lease_blocks_is_declared_default_state(self) -> None:
        self.assertEqual(
            runtime_state.default_state()["worker_worktree_lease_blocks"], {}
        )

    def test_worktree_lease_block_counts_survive_save_and_reload(self) -> None:
        entry = {
            "count": 4,
            "first_at": "2026-08-07T07:51:00Z",
            "last_at": "2026-08-08T06:52:00Z",
            "refresh_status": "task_head_mismatch: local=a remote=b",
            "escalated": False,
        }
        state = runtime_state.default_state()
        state["worker_worktree_lease_blocks"]["odp-orch-example-001"] = entry

        runtime_state.save_runtime_state(self.config, state)
        reloaded = runtime_state.load_runtime_state(self.config)

        self.assertEqual(
            state["worker_worktree_lease_blocks"]["odp-orch-example-001"], entry
        )
        self.assertEqual(
            reloaded["worker_worktree_lease_blocks"]["odp-orch-example-001"], entry
        )

    def test_malformed_worktree_lease_blocks_normalize_instead_of_crashing(self) -> None:
        self.assertEqual(
            runtime_state.migrate_state(
                {"worker_worktree_lease_blocks": "not-a-mapping"}
            )["worker_worktree_lease_blocks"],
            {},
        )
        self.assertEqual(
            runtime_state.migrate_state(
                {
                    "worker_worktree_lease_blocks": {
                        "odp-orch-example-001": {"count": 2},
                        "odp-orch-garbage-001": "junk",
                    }
                }
            )["worker_worktree_lease_blocks"],
            {"odp-orch-example-001": {"count": 2}},
        )


class HandedOffWorkerStatusTests(unittest.TestCase):
    """A retry/fallback parent has delegated its queue event to a successor run."""

    def _worker(self, status: str) -> dict[str, object]:
        return {
            "run_id": f"run-{status}",
            "status": status,
            "task_id": "ODP-ORCH-EXAMPLE-001",
            "queue_event_id": "evt-1",
        }

    def test_retried_parent_is_garbage_collected(self) -> None:
        # `retried` used to match neither the keep-set nor the drop-set, so the
        # record fell through to the trailing `keep[run_id] = worker` and stayed
        # in state.json forever.
        state = {
            "workers": {"run-retried": self._worker("retried")},
            "queue": {"events": {"evt-1": {"status": "completed"}}},
        }
        runtime_state.prune_worker_records(state, {"ODP-ORCH-EXAMPLE-001": "done"})
        self.assertEqual(state["workers"], {})

    def test_fallback_parent_is_kept_while_its_child_runs(self) -> None:
        state = {
            "workers": {"run-fallback": self._worker("fallback")},
            "queue": {"events": {"evt-1": {"status": "started"}}},
        }
        runtime_state.prune_worker_records(state, {"ODP-ORCH-EXAMPLE-001": "in_progress"})
        self.assertIn("run-fallback", state["workers"])

    def test_handed_off_statuses_are_not_double_counted_as_active(self) -> None:
        # `retried` is deliberately NOT here. It is terminal, so poll_workers skips
        # it at the top of the loop and the handed-off guard further down can never
        # see it -- keeping it in this set made it a dead member that read as
        # meaningful. `fallback` is the only status the guard actually acts on.
        self.assertEqual(runtime_state.HANDED_OFF_WORKER_STATUSES, {"fallback"})
        self.assertIn("retried", runtime_state.TERMINAL_WORKER_STATUSES)
        self.assertIn("retried", runtime_state.TERMINAL_WORKER_STATUSES)
        # `fallback` must stay out of the terminal set: the queue event is still
        # in flight through the fallback child, and `_rebuild_queue_records`
        # relies on it being an active status.
        self.assertNotIn("fallback", runtime_state.TERMINAL_WORKER_STATUSES)
        self.assertIn("fallback", runtime_state.ACTIVE_WORKER_STATUSES)


if __name__ == "__main__":
    unittest.main()


class RotationDedupeGarbageCollectionTests(unittest.TestCase):
    """The rotation de-dupe map is anchored to live worker records."""

    def test_entries_for_dropped_workers_are_removed(self) -> None:
        state = {
            "workers": {
                "run-live": {"run_id": "run-live", "status": "running", "task_id": "T-1"},
                "run-gone": {"run_id": "run-gone", "status": "failed", "task_id": "T-2"},
            },
            "queue": {"events": {}},
            "provider_guardrails": {
                "processed_model_rotation_failures": {
                    "run-live": {"provider": "antigravity"},
                    "run-gone": {"provider": "antigravity"},
                    "run-ancient": {"provider": "antigravity"},
                }
            },
        }
        runtime_state.prune_worker_records(state, {"T-1": "in_progress", "T-2": "done"})
        self.assertEqual(
            set(state["provider_guardrails"]["processed_model_rotation_failures"]),
            {"run-live"},
        )

    def test_missing_bucket_is_left_alone(self) -> None:
        state = {"workers": {}, "queue": {"events": {}}, "provider_guardrails": {}}
        runtime_state.prune_worker_records(state, {})
        self.assertNotIn("processed_model_rotation_failures", state["provider_guardrails"])


class WorkerStatusTaxonomyTests(unittest.TestCase):
    """Every worker status must be classified by exactly one axis.

    `retried` was introduced without being added to any set, so it matched
    neither "still working" nor "finished" and silently fell through every
    branch that asked. This test fails the next time that happens.
    """

    # Every value ever assigned to worker["status"], plus the ones set when the
    # record is first created.
    KNOWN_WORKER_STATUSES = {
        "running",
        "started",
        "manual_pending",
        "waiting_approval",
        "suspended_approval",
        "retry_backoff",
        "stalled",
        "fallback",
        "completed",
        "failed",
        "superseded",
        "reassigned",
        "retried",
    }

    def test_every_status_is_active_or_terminal(self) -> None:
        classified = runtime_state.ACTIVE_WORKER_STATUSES | runtime_state.TERMINAL_WORKER_STATUSES
        self.assertEqual(self.KNOWN_WORKER_STATUSES - classified, set())

    def test_active_and_terminal_do_not_overlap(self) -> None:
        self.assertEqual(
            runtime_state.ACTIVE_WORKER_STATUSES & runtime_state.TERMINAL_WORKER_STATUSES,
            set(),
        )

    def test_handed_off_statuses_are_a_subset_of_the_vocabulary(self) -> None:
        self.assertTrue(runtime_state.HANDED_OFF_WORKER_STATUSES <= self.KNOWN_WORKER_STATUSES)


class ActiveWorkerStatusFloorTests(unittest.TestCase):
    """Configuration may widen "still working"; it may not narrow it.

    Sixteen call sites ask this question -- can this queue event be finalized, is
    this agent at capacity, is a worker using this worktree -- and all sixteen are
    dangerous in the same direction: treating a live worker as finished. The
    shipped `ready_dispatcher.active_worker_statuses` was missing `fallback` and
    `started`, so a parent that had handed off to a file_inbox child did not
    protect its own queue event.
    """

    def test_configured_list_cannot_drop_below_the_floor(self) -> None:
        for configured in ([], ["running"], ["running", "stalled"]):
            with self.subTest(configured=configured):
                config = {"ready_dispatcher": {"active_worker_statuses": configured}}
                self.assertTrue(
                    runtime_state.ACTIVE_WORKER_STATUSES
                    <= runtime_state.active_worker_statuses(config)
                )

    def test_missing_key_and_null_still_give_the_floor(self) -> None:
        for config in ({}, {"ready_dispatcher": {}}, {"ready_dispatcher": {"active_worker_statuses": None}}):
            with self.subTest(config=config):
                self.assertEqual(
                    runtime_state.active_worker_statuses(config),
                    runtime_state.ACTIVE_WORKER_STATUSES,
                )

    def test_configuration_can_still_add(self) -> None:
        config = {"ready_dispatcher": {"active_worker_statuses": ["a_custom_status"]}}
        resolved = runtime_state.active_worker_statuses(config)
        self.assertIn("a_custom_status", resolved)
        self.assertTrue(runtime_state.ACTIVE_WORKER_STATUSES <= resolved)

    def test_the_floor_covers_the_statuses_that_were_missing(self) -> None:
        """`fallback` and `started` are the two the shipped config omitted."""
        for status in ("fallback", "started"):
            with self.subTest(status=status):
                self.assertIn(status, runtime_state.active_worker_statuses({}))

    # `compute_mode_occupancy` is the one legitimate raw reader. It answers
    # "does the execution lane look busy", not "is this worker still live", and a
    # manual-inbox record parked with no PID must not hold focus on execution.
    # Everything else is a safety question where narrowing is the dangerous
    # direction.
    RAW_READER_EXEMPTIONS = {"supervisor.py"}

    def test_no_safety_caller_reads_the_raw_setting(self) -> None:
        """A re-added direct read reintroduces the narrowing, so fail here instead."""
        package = Path(runtime_state.__file__).parent
        offenders = []
        for path in sorted(package.glob("*.py")):
            if (
                path.name.startswith("test_")
                or path.name == "runtime_state.py"
                or path.name in self.RAW_READER_EXEMPTIONS
            ):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if 'get("active_worker_statuses"' in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(
            offenders,
            [],
            f"{offenders} read the raw setting; call "
            "runtime_state.active_worker_statuses(config) so the floor applies",
        )

    def test_the_exemption_is_only_compute_mode_occupancy(self) -> None:
        """Keep the exemption honest: one reader, in one function."""
        source = (Path(runtime_state.__file__).parent / "supervisor.py").read_text(encoding="utf-8")
        raw_reads = [
            number
            for number, line in enumerate(source.splitlines(), 1)
            if 'get("active_worker_statuses"' in line
        ]
        self.assertEqual(len(raw_reads), 1, f"supervisor.py raw reads at {raw_reads}")
        tree = ast.parse(source)
        owning = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.lineno <= raw_reads[0] <= node.end_lineno
        ]
        self.assertIn("compute_mode_occupancy", owning)


class QuotaRecoveryRuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.config = {
            "paths": {
                "state_file": str(self.root / "state.json"),
                "event_queue": str(self.root / "event-queue.jsonl"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "account_pools": {"pool_a": {"max_concurrent": 2, "state": "healthy", "enabled": True}},
            "agents": {"codex": {"id": "codex", "provider": "codex", "account_pool": "pool_a"}},
            "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex"}},
        }

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_merge_runtime_states_drops_stale_dispatch_pause_after_clearance(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["provider_guardrails"]["cleared_pauses"]["codex"] = {
            "provider": "codex",
            "cleared_at": "2026-09-11T02:04:50Z",
            "cleared_paused_at": "2026-09-11T01:37:15Z",
            "worker_run_id": "run-001",
        }

        in_mem_state = runtime_state.default_state()
        in_mem_state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": "2026-09-11T01:37:15Z",
            "blocked_until": "2026-09-11T02:37:15Z",
            "failure_kind": "quota_terminal",
        }

        merged = runtime_state.merge_runtime_states(disk_state, in_mem_state)

        self.assertNotIn("codex", merged["provider_guardrails"]["dispatch_pauses"])
        self.assertNotIn("codex", in_mem_state["provider_guardrails"]["dispatch_pauses"])
        self.assertIn("codex", merged["provider_guardrails"]["cleared_pauses"])

    def test_merge_runtime_states_preserves_new_failure_after_clearance(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["provider_guardrails"]["cleared_pauses"]["codex"] = {
            "provider": "codex",
            "cleared_at": "2026-09-11T02:04:50Z",
            "cleared_paused_at": "2026-09-11T01:37:15Z",
        }

        in_mem_state = runtime_state.default_state()
        in_mem_state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": "2026-09-11T02:10:00Z",
            "blocked_until": "2026-09-11T02:40:00Z",
            "failure_kind": "quota_terminal",
        }

        merged = runtime_state.merge_runtime_states(disk_state, in_mem_state)

        self.assertIn("codex", merged["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(
            merged["provider_guardrails"]["dispatch_pauses"]["codex"]["paused_at"],
            "2026-09-11T02:10:00Z",
        )

    def test_merge_runtime_states_preserves_concurrent_new_pause_on_disk(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": "2026-09-11T02:10:00Z",
            "blocked_until": "2026-09-11T02:40:00Z",
            "failure_kind": "quota_terminal",
        }

        in_mem_state = runtime_state.default_state()
        in_mem_state["provider_guardrails"]["cleared_pauses"]["codex"] = {
            "provider": "codex",
            "cleared_at": "2026-09-11T02:04:50Z",
            "cleared_paused_at": "2026-09-11T01:37:15Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, in_mem_state)

        self.assertIn("codex", merged["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(
            merged["provider_guardrails"]["dispatch_pauses"]["codex"]["paused_at"],
            "2026-09-11T02:10:00Z",
        )

    def test_merge_runtime_states_account_pool_recovering_wins_over_stale_cooldown(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "recovering",
            "generation": 1,
            "effective_concurrency": 1,
            "last_probe_at": "2026-09-11T02:04:50Z",
        }

        in_mem_state = runtime_state.default_state()
        in_mem_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "cooldown",
            "generation": 1,
            "effective_concurrency": 0,
            "last_failure_at": "2026-09-11T01:37:15Z",
            "next_probe_at": "2026-09-11T02:37:15Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, in_mem_state)

        self.assertEqual(merged["account_pool_runtime"]["codex_bjoe"]["state"], "recovering")
        self.assertEqual(merged["account_pool_runtime"]["codex_bjoe"]["effective_concurrency"], 1)

    def test_merge_runtime_states_account_pool_new_generation_wins(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "recovering",
            "generation": 1,
            "effective_concurrency": 1,
        }

        in_mem_state = runtime_state.default_state()
        in_mem_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "cooldown",
            "generation": 2,
            "effective_concurrency": 0,
            "last_failure_at": "2026-09-11T02:10:00Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, in_mem_state)

        self.assertEqual(merged["account_pool_runtime"]["codex_bjoe"]["generation"], 2)
        self.assertEqual(merged["account_pool_runtime"]["codex_bjoe"]["state"], "cooldown")
        self.assertEqual(merged["account_pool_runtime"]["codex_bjoe"]["effective_concurrency"], 0)

    def test_interleaved_save_runtime_state_prevents_lost_clear_resurrection(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        initial = runtime_state.default_state()
        initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": "2026-09-11T01:37:15Z",
            "blocked_until": "2026-09-11T02:37:15Z",
            "failure_kind": "quota_terminal",
        }
        initial["account_pool_runtime"]["codex_bjoe"] = {
            "state": "cooldown",
            "generation": 1,
            "effective_concurrency": 0,
            "last_failure_at": "2026-09-11T01:37:15Z",
            "next_probe_at": "2026-09-11T02:37:15Z",
        }
        runtime_state.save_runtime_state(self.config, initial)

        # Writer A loads state with old pause and cooldown
        writer_a_state = runtime_state.load_runtime_state(self.config)

        # Writer B loads and clears provider pause
        writer_b_state = runtime_state.load_runtime_state(self.config)
        writer_b_state["provider_guardrails"]["dispatch_pauses"].pop("codex", None)
        writer_b_state["provider_guardrails"]["cleared_pauses"]["codex"] = {
            "provider": "codex",
            "cleared_at": "2026-09-11T02:04:50Z",
            "cleared_paused_at": "2026-09-11T01:37:15Z",
        }
        writer_b_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "recovering",
            "generation": 1,
            "effective_concurrency": 1,
            "last_probe_at": "2026-09-11T02:04:50Z",
        }
        runtime_state.save_runtime_state(self.config, writer_b_state)

        # Writer A performs a save from its stale in-memory state
        runtime_state.save_runtime_state(self.config, writer_a_state)

        # The cleared state on disk must not have been resurrected
        reloaded = runtime_state.load_runtime_state(self.config)
        self.assertNotIn("codex", reloaded["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(reloaded["account_pool_runtime"]["codex_bjoe"]["state"], "recovering")
        self.assertEqual(reloaded["account_pool_runtime"]["codex_bjoe"]["effective_concurrency"], 1)

        # Writer A subsequently observes a new quota failure at 02:10:00Z and saves
        writer_a_state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": "2026-09-11T02:10:00Z",
            "blocked_until": "2026-09-11T02:40:00Z",
            "failure_kind": "quota_terminal",
        }
        writer_a_state["account_pool_runtime"]["codex_bjoe"] = {
            "state": "cooldown",
            "generation": 2,
            "effective_concurrency": 0,
            "last_failure_at": "2026-09-11T02:10:00Z",
        }
        runtime_state.save_runtime_state(self.config, writer_a_state)

        # The new failure must be preserved on disk
        reloaded_after_failure = runtime_state.load_runtime_state(self.config)
        self.assertIn("codex", reloaded_after_failure["provider_guardrails"]["dispatch_pauses"])
        self.assertEqual(
            reloaded_after_failure["provider_guardrails"]["dispatch_pauses"]["codex"]["paused_at"],
            "2026-09-11T02:10:00Z",
        )
        self.assertEqual(
            reloaded_after_failure["account_pool_runtime"]["codex_bjoe"]["generation"],
            2,
        )
        self.assertEqual(
            reloaded_after_failure["account_pool_runtime"]["codex_bjoe"]["state"],
            "cooldown",
        )

    def test_same_second_new_run_failure_survives_clear(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2026-09-11T01:37:15Z"
        clear_time = "2026-09-11T02:04:50Z"
        initial = runtime_state.default_state()
        initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": old_time,
            "blocked_until": "2026-09-11T02:37:15Z",
            "worker_run_id": "old-run",
            "failure_kind": "quota_terminal",
        }
        runtime_state.save_runtime_state(self.config, initial)

        stale = runtime_state.load_runtime_state(self.config)
        fresh = runtime_state.load_runtime_state(self.config)
        fresh["provider_guardrails"]["dispatch_pauses"].pop("codex", None)
        fresh["provider_guardrails"]["cleared_pauses"]["codex"] = {
            "provider": "codex",
            "cleared_at": clear_time,
            "cleared_paused_at": old_time,
            "worker_run_id": "old-run",
        }
        runtime_state.save_runtime_state(self.config, fresh)

        # Stale writer records a NEW failure in the same second with a new run ID
        stale["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex",
            "paused_at": clear_time,
            "blocked_until": "2026-09-11T02:34:50Z",
            "worker_run_id": "new-run",
            "failure_kind": "quota_terminal",
        }
        runtime_state.save_runtime_state(self.config, stale)

        reloaded = runtime_state.load_runtime_state(self.config)
        pauses = reloaded["provider_guardrails"]["dispatch_pauses"]
        self.assertIn("codex", pauses)
        self.assertEqual(pauses["codex"]["worker_run_id"], "new-run")
        self.assertEqual(pauses["codex"]["paused_at"], clear_time)

    def test_task_failure_streak_reset_survives_save(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        initial = runtime_state.default_state()
        initial["provider_guardrails"]["task_failure_streaks"]["TASK-1:codex"] = {
            "task_id": "TASK-1",
            "provider": "codex",
            "count": 2,
            "last_reason": "Failure streak",
        }
        runtime_state.save_runtime_state(self.config, initial)

        current = runtime_state.load_runtime_state(self.config)
        self.assertIn("TASK-1:codex", current["provider_guardrails"]["task_failure_streaks"])

        # Reset the streak (e.g. after successful task completion)
        current["provider_guardrails"]["task_failure_streaks"].pop("TASK-1:codex", None)
        runtime_state.save_runtime_state(self.config, current)

        reloaded = runtime_state.load_runtime_state(self.config)
        self.assertNotIn("TASK-1:codex", reloaded["provider_guardrails"]["task_failure_streaks"])

    def test_stale_clear_preserves_concurrent_new_failure_saved_before_clear(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2026-09-11T01:37:15Z"
        new_time = "2026-09-11T02:04:49Z"
        clear_time = "2026-09-11T02:04:50Z"
        future_time = "2099-09-11T02:37:15Z"

        initial = runtime_state.default_state()
        initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex", "trigger_provider": "codex", "paused_at": old_time,
            "blocked_until": future_time, "failure_kind": "quota_terminal",
            "worker_run_id": "old-run", "task_id": "REVIEW-TASK", "auth_identity_hash": "auth-a",
        }
        initial["account_pool_runtime"]["pool_a"] = {
            "state": "cooldown", "effective_concurrency": 0, "generation": 1,
            "last_failure_at": old_time, "next_probe_at": future_time, "last_worker_run_id": "old-run",
            "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
        }
        runtime_state.save_runtime_state(self.config, initial)
        clearer = runtime_state.load_runtime_state(self.config)
        failure_writer = runtime_state.load_runtime_state(self.config)

        # A new failure is durable after the CLI read but before its clear/save.
        failure_writer["provider_guardrails"]["dispatch_pauses"]["codex"].update(
            paused_at=new_time, worker_run_id="new-run",
        )
        failure_writer["account_pool_runtime"]["pool_a"].update(
            generation=2, last_worker_run_id="new-run", last_failure_at=new_time,
        )
        runtime_state.save_runtime_state(self.config, failure_writer)

        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, clearer, "codex"))
        runtime_state.save_runtime_state(self.config, clearer)

        loaded = runtime_state.load_runtime_state(self.config)
        self.assertEqual(loaded["account_pool_runtime"]["pool_a"]["generation"], 2)
        self.assertEqual(
            loaded["provider_guardrails"]["dispatch_pauses"].get("codex", {}).get("worker_run_id"),
            "new-run",
        )

    def test_same_second_new_failure_survives_later_old_snapshot_save(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        clear_time = "2026-09-11T02:04:50Z"
        future_time = "2099-09-11T02:37:15Z"
        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex", "trigger_provider": "codex", "paused_at": clear_time,
            "blocked_until": future_time, "failure_kind": "quota_terminal",
            "worker_run_id": "old-run", "task_id": "REVIEW-TASK", "auth_identity_hash": "auth-a",
        }
        state["account_pool_runtime"]["pool_a"] = {
            "state": "cooldown", "effective_concurrency": 0, "generation": 1,
            "last_failure_at": clear_time, "next_probe_at": future_time, "last_worker_run_id": "old-run",
            "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
        }
        runtime_state.save_runtime_state(self.config, state)
        stale_old = runtime_state.load_runtime_state(self.config)
        clearer = runtime_state.load_runtime_state(self.config)
        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, clearer, "codex"))
        runtime_state.save_runtime_state(self.config, clearer)

        new_writer = runtime_state.load_runtime_state(self.config)
        new_pause = deepcopy(stale_old["provider_guardrails"]["dispatch_pauses"]["codex"])
        new_pause["worker_run_id"] = "new-run"
        new_writer["provider_guardrails"]["dispatch_pauses"]["codex"] = new_pause
        runtime_state.save_runtime_state(self.config, new_writer)
        loaded = runtime_state.load_runtime_state(self.config)
        self.assertEqual(loaded["provider_guardrails"]["dispatch_pauses"]["codex"]["worker_run_id"], "new-run")

        runtime_state.save_runtime_state(self.config, stale_old)
        loaded = runtime_state.load_runtime_state(self.config)
        self.assertEqual(
            loaded["provider_guardrails"]["dispatch_pauses"].get("codex", {}).get("worker_run_id"),
            "new-run",
        )

    def test_disk_canary_success_survives_stale_recovering_writer(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2026-09-11T01:37:15Z"
        clear_time = "2026-09-11T02:04:50Z"
        future_time = "2099-09-11T02:37:15Z"
        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex", "trigger_provider": "codex", "paused_at": old_time,
            "blocked_until": future_time, "failure_kind": "quota_terminal",
            "worker_run_id": "old-run", "task_id": "REVIEW-TASK", "auth_identity_hash": "auth-a",
        }
        state["account_pool_runtime"]["pool_a"] = {
            "state": "cooldown", "effective_concurrency": 0, "generation": 1,
            "last_failure_at": old_time, "next_probe_at": future_time, "last_worker_run_id": "old-run",
            "auth_identity_hash": "auth-a", "failure_kind": "quota_terminal",
        }
        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, state, "codex"))
        runtime_state.save_runtime_state(self.config, state)
        stale_recovering = runtime_state.load_runtime_state(self.config)
        successful_writer = runtime_state.load_runtime_state(self.config)
        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-09-11T02:05:30Z"),
        ):
            self.assertTrue(
                supervisor.record_account_pool_canary_success(
                    self.config,
                    successful_writer,
                    {"logical_agent_id": "codex", "run_id": "canary-run", "task_id": "REVIEW-TASK", "auth_identity_hash": "auth-a"},
                )
            )
        runtime_state.save_runtime_state(self.config, successful_writer)
        self.assertEqual(
            runtime_state.load_runtime_state(self.config)["account_pool_runtime"]["pool_a"]["state"],
            "healthy",
        )

        runtime_state.save_runtime_state(self.config, stale_recovering)
        pool = runtime_state.load_runtime_state(self.config)["account_pool_runtime"]["pool_a"]
        self.assertEqual(pool["state"], "healthy")
        self.assertEqual(pool["effective_concurrency"], 2)

    def test_multi_clear_earlier_clear_survives_later_clear_and_stale_writer(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        def pause(at, run, auth="auth-a"):
            return {
                "provider": "codex", "trigger_provider": "codex", "paused_at": at,
                "blocked_until": "2099-09-11T02:37:15Z", "worker_run_id": run,
                "auth_identity_hash": auth, "failure_kind": "quota_terminal",
            }
        def clear_pause(state, at):
            with (
                mock.patch.object(supervisor, "utc_now", return_value=at),
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, state, "codex"))
            runtime_state.save_runtime_state(self.config, state)

        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = pause("2026-09-11T01:37:15Z", "run-a")
        runtime_state.save_runtime_state(self.config, state)
        stale_a = runtime_state.load_runtime_state(self.config)
        clear_pause(state, "2026-09-11T02:04:50Z")
        self.assertNotIn("codex", runtime_state.load_runtime_state(self.config)["provider_guardrails"]["dispatch_pauses"])

        state["provider_guardrails"]["dispatch_pauses"]["codex"] = pause("2026-09-11T02:05:00Z", "run-b", "auth-b")
        runtime_state.save_runtime_state(self.config, state)
        clear_pause(state, "2026-09-11T02:06:00Z")
        self.assertNotIn("codex", runtime_state.load_runtime_state(self.config)["provider_guardrails"]["dispatch_pauses"])

        runtime_state.save_runtime_state(self.config, stale_a)
        self.assertNotIn("codex", runtime_state.load_runtime_state(self.config)["provider_guardrails"]["dispatch_pauses"])

    def test_delayed_old_clear_does_not_replace_newer_failure_clear(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        def pause(at, run, auth="auth-a"):
            return {
                "provider": "codex", "trigger_provider": "codex", "paused_at": at,
                "blocked_until": "2099-09-11T02:37:15Z", "worker_run_id": run,
                "auth_identity_hash": auth, "failure_kind": "quota_terminal",
            }
        def clear_pause(state, at):
            with (
                mock.patch.object(supervisor, "utc_now", return_value=at),
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, state, "codex"))
            runtime_state.save_runtime_state(self.config, state)

        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = pause("2026-09-11T01:37:15Z", "run-a")
        runtime_state.save_runtime_state(self.config, state)
        delayed_clearer_a = runtime_state.load_runtime_state(self.config)
        clear_pause(state, "2026-09-11T02:04:50Z")
        state["provider_guardrails"]["dispatch_pauses"]["codex"] = pause("2026-09-11T02:05:00Z", "run-b")
        runtime_state.save_runtime_state(self.config, state)
        stale_b = deepcopy(state)
        clear_pause(state, "2026-09-11T02:06:00Z")
        clear_pause(delayed_clearer_a, "2026-09-11T02:07:00Z")
        runtime_state.save_runtime_state(self.config, stale_b)
        self.assertNotIn("codex", runtime_state.load_runtime_state(self.config)["provider_guardrails"]["dispatch_pauses"])

    def test_disk_clear_preserves_shared_auth_canary_fence(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        config = deepcopy(self.config)
        config["account_pools"]["pool_b"] = {"max_concurrent": 2, "state": "healthy", "enabled": True}
        config["agents"]["codex2"] = {"id": "codex2", "provider": "codex2", "account_pool": "pool_b"}
        config["providers"]["codex2"] = {"delivery_mode": "codex", "quota_group": "codex"}

        initial = runtime_state.default_state()
        initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex", "trigger_provider": "codex", "paused_at": "2026-09-11T01:37:15Z",
            "blocked_until": "2099-09-11T02:37:15Z", "worker_run_id": "failed-run",
            "auth_identity_hash": "same-auth", "failure_kind": "quota_terminal",
        }
        initial["account_pool_runtime"]["pool_a"] = {
            "state": "cooldown", "effective_concurrency": 0, "generation": 1,
            "last_failure_at": "2026-09-11T01:37:15Z", "next_probe_at": "2099-09-11T02:37:15Z",
            "last_worker_run_id": "failed-run", "auth_identity_hash": "same-auth",
            "failure_kind": "quota_terminal",
        }
        initial["account_pool_runtime"]["pool_b"] = {
            "state": "healthy", "effective_concurrency": 2,
            "auth_identity_hash": "same-auth", "generation": 0,
        }
        runtime_state.save_runtime_state(config, initial)

        clearer = runtime_state.load_runtime_state(config)
        with (
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="same-auth"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "utc_now", return_value="2026-09-11T02:04:50Z"),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(config, clearer, "codex"))
        self.assertEqual(clearer["account_pool_runtime"]["pool_b"]["state"], "recovering")
        self.assertEqual(clearer["account_pool_runtime"]["pool_b"]["effective_concurrency"], 0)
        runtime_state.save_runtime_state(config, clearer)

        restored = runtime_state.load_runtime_state(config)
        self.assertNotIn("codex", restored["provider_guardrails"]["dispatch_pauses"])
        with mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="same-auth"):
            slots = [supervisor.account_pool_effective_concurrency(config, restored, a) for a in ("codex", "codex2")]
        self.assertLessEqual(sum(slots), 1, f"first clear/save restored shared-auth slots {slots} before any success")

    def test_new_failure_epoch_survives_stale_clear_pool_merge_both_save_orders(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2099-09-11T01:37:15Z"
        clear_time = "2099-09-11T02:04:50Z"
        new_time = "2099-09-11T02:04:49Z"
        future_time = "2099-09-11T03:37:15Z"

        for failure_saved_first in (True, False):
            (self.root / "state.json").unlink(missing_ok=True)
            initial = runtime_state.default_state()
            initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
                "provider": "codex", "trigger_provider": "codex", "paused_at": old_time,
                "blocked_until": future_time, "failure_kind": "quota_terminal",
                "worker_run_id": "old-run", "auth_identity_hash": "auth-a",
            }
            initial["account_pool_runtime"]["pool_a"] = {
                "state": "cooldown", "effective_concurrency": 0, "generation": 1,
                "last_failure_at": old_time, "next_probe_at": future_time,
                "last_worker_run_id": "old-run", "auth_identity_hash": "auth-a",
                "failure_kind": "quota_terminal",
            }
            runtime_state.save_runtime_state(self.config, initial)

            clearer = runtime_state.load_runtime_state(self.config)
            failure_writer = runtime_state.load_runtime_state(self.config)

            worker = {"run_id": "new-run", "logical_agent_id": "codex", "provider": "codex"}
            with (
                mock.patch.object(supervisor, "utc_now", return_value=new_time),
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
            ):
                self.assertTrue(supervisor.mark_provider_dispatch_paused(
                    self.config, failure_writer, "codex", "usage limit reached",
                    worker_run_id="new-run", worker=worker, failure_kind="quota_terminal",
                ))

            with (
                mock.patch.object(supervisor, "utc_now", return_value=clear_time),
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, clearer, "codex"))

            first, second = (failure_writer, clearer) if failure_saved_first else (clearer, failure_writer)
            runtime_state.save_runtime_state(self.config, first)
            runtime_state.save_runtime_state(self.config, second)

            restored = runtime_state.load_runtime_state(self.config)
            self.assertEqual(restored["provider_guardrails"]["dispatch_pauses"]["codex"]["worker_run_id"], "new-run")
            pool = restored["account_pool_runtime"]["pool_a"]
            self.assertEqual((pool["state"], pool["last_worker_run_id"], pool["effective_concurrency"]), ("cooldown", "new-run", 0))

    def test_post_canary_new_failure_survives_equal_generation_recovery(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2099-09-11T01:37:15Z"
        clear_time = "2099-09-11T02:04:50Z"
        canary_time = "2099-09-11T02:05:00Z"
        new_time = "2099-09-11T02:06:00Z"
        future_time = "2099-09-11T03:37:15Z"

        initial = runtime_state.default_state()
        initial["provider_guardrails"]["dispatch_pauses"]["codex"] = {
            "provider": "codex", "trigger_provider": "codex", "paused_at": old_time,
            "blocked_until": future_time, "failure_kind": "quota_terminal",
            "worker_run_id": "old-run", "auth_identity_hash": "auth-a",
        }
        initial["account_pool_runtime"]["pool_a"] = {
            "state": "cooldown", "effective_concurrency": 0, "generation": 1,
            "last_failure_at": old_time, "next_probe_at": future_time,
            "last_worker_run_id": "old-run", "auth_identity_hash": "auth-a",
            "failure_kind": "quota_terminal",
        }
        runtime_state.save_runtime_state(self.config, initial)

        stale_failure_writer = runtime_state.load_runtime_state(self.config)
        clearer = runtime_state.load_runtime_state(self.config)
        with (
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(self.config, clearer, "codex"))
        runtime_state.save_runtime_state(self.config, clearer)

        with (
            mock.patch.object(supervisor, "utc_now", return_value=canary_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.record_account_pool_canary_success(
                self.config, clearer, {"logical_agent_id": "codex", "run_id": "canary-run", "started_at": canary_time, "auth_identity_hash": "auth-a"}
            ))
        runtime_state.save_runtime_state(self.config, clearer)

        worker = {"run_id": "new-run", "logical_agent_id": "codex", "provider": "codex"}
        with (
            mock.patch.object(supervisor, "utc_now", return_value=new_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
        ):
            self.assertTrue(supervisor.mark_provider_dispatch_paused(
                self.config, stale_failure_writer, "codex", "usage limit reached",
                worker_run_id="new-run", worker=worker, failure_kind="quota_terminal",
            ))
        runtime_state.save_runtime_state(self.config, stale_failure_writer)

        restored = runtime_state.load_runtime_state(self.config)
        self.assertEqual(restored["provider_guardrails"]["dispatch_pauses"]["codex"]["worker_run_id"], "new-run")
        pool = restored["account_pool_runtime"]["pool_a"]
        self.assertEqual((pool["state"], pool["last_worker_run_id"], pool["effective_concurrency"]), ("cooldown", "new-run", 0))

    def test_blind_clear_does_not_erase_new_failure_in_same_second(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        clear_time = "2099-09-11T02:04:50Z"
        state = runtime_state.default_state()
        runtime_state.save_runtime_state(self.config, state)
        clearer = runtime_state.load_runtime_state(self.config)
        with (
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertFalse(supervisor.clear_provider_dispatch_pause(self.config, clearer, "codex"))
        runtime_state.save_runtime_state(self.config, clearer)

        writer = runtime_state.load_runtime_state(self.config)
        worker = {"run_id": "new-run", "logical_agent_id": "codex", "provider": "codex"}
        with (
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
        ):
            self.assertTrue(supervisor.mark_provider_dispatch_paused(
                self.config, writer, "codex", "usage limit reached",
                worker_run_id="new-run", worker=worker, failure_kind="quota_terminal",
            ))
        runtime_state.save_runtime_state(self.config, writer)

        restored = runtime_state.load_runtime_state(self.config)
        self.assertEqual(restored["provider_guardrails"]["dispatch_pauses"]["codex"]["worker_run_id"], "new-run")
        pool = restored["account_pool_runtime"]["pool_a"]
        self.assertEqual((pool["state"], pool["last_worker_run_id"], pool["effective_concurrency"]), ("cooldown", "new-run", 0))

    def test_alias_clear_with_existing_legacy_pause_preserves_new_group_failure(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2099-09-11T01:37:15Z"
        clear_time = "2099-09-11T02:04:50Z"
        future_time = "2099-09-11T03:37:15Z"
        config = deepcopy(self.config)
        config["providers"]["codex2"] = {"delivery_mode": "codex", "quota_group": "codex"}
        config["agents"]["codex2"] = {"id": "codex2", "provider": "codex2", "account_pool": "pool_a"}

        state = runtime_state.default_state()
        state["provider_guardrails"]["dispatch_pauses"]["codex2"] = {
            "provider": "codex2", "trigger_provider": "codex2", "paused_at": old_time,
            "blocked_until": future_time, "failure_kind": "quota_terminal",
            "worker_run_id": "legacy-alias-run", "auth_identity_hash": "auth-a",
        }
        runtime_state.save_runtime_state(config, state)

        clearer = runtime_state.load_runtime_state(config)
        with (
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            self.assertTrue(supervisor.clear_provider_dispatch_pause(config, clearer, "codex2"))
        self.assertIsNone(clearer["provider_guardrails"]["cleared_pauses"]["codex"]["cleared_paused_at"])
        runtime_state.save_runtime_state(config, clearer)

        writer = runtime_state.load_runtime_state(config)
        worker = {"run_id": "new-run", "logical_agent_id": "codex", "provider": "codex"}
        with (
            mock.patch.object(supervisor, "utc_now", return_value=clear_time),
            mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
        ):
            self.assertTrue(supervisor.mark_provider_dispatch_paused(
                config, writer, "codex", "usage limit reached",
                worker_run_id="new-run", worker=worker, failure_kind="quota_terminal",
            ))
        runtime_state.save_runtime_state(config, writer)

        restored = runtime_state.load_runtime_state(config)
        self.assertEqual(restored["provider_guardrails"]["dispatch_pauses"].get("codex", {}).get("worker_run_id"), "new-run")

    def test_distinct_failure_run_survives_stale_clear_same_second(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        old_time = "2099-09-11T02:04:50Z"
        new_time = "2099-09-11T02:04:50Z"
        config = deepcopy(self.config)

        for failure_saved_first in (True, False):
            Path(config["paths"]["state_file"]).write_text("{}", encoding="utf-8")
            state = runtime_state.default_state()
            state["provider_guardrails"]["dispatch_pauses"]["codex"] = {
                "provider": "codex", "trigger_provider": "codex", "paused_at": old_time,
                "blocked_until": "2099-09-11T03:37:15Z", "failure_kind": "quota_terminal",
                "worker_run_id": "old-run", "auth_identity_hash": "auth-a",
            }
            state["account_pool_runtime"]["pool_a"] = {
                "state": "cooldown", "effective_concurrency": 0, "generation": 1,
                "last_failure_at": old_time, "next_probe_at": "2099-09-11T03:37:15Z",
                "last_worker_run_id": "old-run", "auth_identity_hash": "auth-a",
                "failure_kind": "quota_terminal",
            }
            runtime_state.save_runtime_state(config, state)
            clearer = runtime_state.load_runtime_state(config)
            failure_writer = runtime_state.load_runtime_state(config)

            with (
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor, "utc_now", return_value=new_time),
                mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
            ):
                supervisor.mark_provider_dispatch_paused(
                    config, failure_writer, "codex", "usage limit reached", worker_run_id="new-run",
                    worker={"run_id": "new-run", "logical_agent_id": "codex", "provider": "codex"},
                    failure_kind="quota_terminal",
                )
            with (
                mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="auth-a"),
                mock.patch.object(supervisor, "write_activity_log"),
                mock.patch.object(supervisor, "utc_now", return_value="2099-09-11T02:04:52Z"),
            ):
                self.assertTrue(supervisor.clear_provider_dispatch_pause(config, clearer, "codex"))

            snapshots = (failure_writer, clearer) if failure_saved_first else (clearer, failure_writer)
            for snapshot in snapshots:
                runtime_state.save_runtime_state(config, snapshot)
            restored = runtime_state.load_runtime_state(config)
            self.assertEqual(restored["provider_guardrails"]["dispatch_pauses"]["codex"]["worker_run_id"], "new-run")
            pool = restored["account_pool_runtime"]["pool_a"]
            self.assertEqual(
                (pool["state"], pool["last_worker_run_id"], pool["effective_concurrency"]),
                ("cooldown", "new-run", 0),
            )
            self.assertEqual(pool["last_failure_at"], new_time)
            self.assertEqual(pool["next_probe_at"], "2099-09-11T03:37:15Z")


@pytest.mark.parametrize("stale_on_disk", [False, True])
def test_cleared_old_run_cooldown_cannot_resurrect_after_new_run_also_cleared(tmp_path, stale_on_disk):
    config = {
        "paths": {
            "state_file": str(tmp_path / "state.json"),
            "event_queue": str(tmp_path / "events.jsonl"),
            "activity_log": str(tmp_path / "activity.jsonl"),
        },
        "account_pools": {"pool_a": {"max_concurrent": 2, "state": "healthy", "enabled": True}},
        "agents": {"codex": {"id": "codex", "provider": "codex", "account_pool": "pool_a"}},
        "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex"}},
    }
    Path(config["paths"]["event_queue"]).write_text("")
    state = runtime_state.default_state()
    failure_time = "2099-09-11T02:04:50Z"
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="fixture-auth"),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
    ):
        with mock.patch.object(supervisor, "utc_now", return_value=failure_time):
            supervisor.mark_provider_dispatch_paused(
                config, state, "codex", "usage limit reached", worker_run_id="run-a",
                worker={"run_id": "run-a", "logical_agent_id": "codex", "provider": "codex"},
                failure_kind="quota_terminal",
            )
        runtime_state.save_runtime_state(config, state)
        stale_run_a = runtime_state.load_runtime_state(config)

        with mock.patch.object(supervisor, "utc_now", return_value=failure_time):
            assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        runtime_state.save_runtime_state(config, state)

        with mock.patch.object(supervisor, "utc_now", return_value=failure_time):
            supervisor.mark_provider_dispatch_paused(
                config, state, "codex", "usage limit reached", worker_run_id="run-b",
                worker={"run_id": "run-b", "logical_agent_id": "codex", "provider": "codex"},
                failure_kind="quota_terminal",
            )
        runtime_state.save_runtime_state(config, state)
        assert state["account_pool_runtime"]["pool_a"]["last_worker_run_id"] == "run-b"

        with mock.patch.object(supervisor, "utc_now", return_value="2099-09-11T02:04:52Z"):
            assert supervisor.clear_provider_dispatch_pause(config, state, "codex")
        runtime_state.save_runtime_state(config, state)
        before_stale_save = deepcopy(state["account_pool_runtime"]["pool_a"])
        assert before_stale_save["state"] == "recovering"
        assert before_stale_save["last_worker_run_id"] == "run-b"

        if stale_on_disk:
            Path(config["paths"]["state_file"]).write_text(json.dumps(stale_run_a))
            runtime_state.save_runtime_state(config, state)
        else:
            runtime_state.save_runtime_state(config, stale_run_a)
        restored = runtime_state.load_runtime_state(config)
        assert not restored["provider_guardrails"]["dispatch_pauses"]
        assert restored["account_pool_runtime"]["pool_a"] == before_stale_save


def _quota_auth_config():
    return {
        "account_pools": {
            "pool_a": {"max_concurrent": 3, "enabled": True},
            "pool_b": {"max_concurrent": 3, "enabled": True},
        },
        "agents": {
            "codex": {"id": "codex", "provider": "codex", "account_pool": "pool_a"},
            "codex2": {"id": "codex2", "provider": "codex2", "account_pool": "pool_b"},
        },
        "providers": {
            "codex": {"delivery_mode": "codex", "quota_group": "codex"},
            "codex2": {"delivery_mode": "codex", "quota_group": "codex"},
        },
    }


@pytest.mark.parametrize("current_auth,pool_auth", [("auth-a", "auth-a"), ("auth-b", "auth-a"), ("auth-b", "auth-b")])
def test_own_pool_success_requires_current_dispatch_auth(current_auth, pool_auth):
    config = _quota_auth_config()
    state = {"account_pool_runtime": {
        "pool_a": {
            "state": "recovering", "effective_concurrency": 1,
            "auth_identity_hash": pool_auth, "last_probe_at": "2026-09-11T02:05:00Z",
        },
    }}
    worker = {
        "run_id": "canary-on-a", "logical_agent_id": "codex", "provider": "codex",
        "started_at": "2026-09-11T02:05:00Z", "auth_identity_hash": "auth-a",
        "status": "completed", "runner_status": "completed", "exit_code": 0,
    }
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value=current_auth),
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        supervisor.record_account_pool_canary_success(config, state, worker)
        capacity = supervisor.account_pool_effective_concurrency(config, state, "codex")
    if current_auth == "auth-a":
        assert capacity == 3
    else:
        assert capacity <= 1, "Auth A success restored full capacity for current, unverified auth B"


@pytest.mark.parametrize("current_auth", ["auth-a", "auth-b"])
def test_late_failure_does_not_fence_new_auth_sibling(current_auth):
    config = _quota_auth_config()
    state = {"account_pool_runtime": {
        "pool_a": {"state": "healthy", "effective_concurrency": 3, "auth_identity_hash": current_auth},
        "pool_b": {"state": "healthy", "effective_concurrency": 3, "auth_identity_hash": current_auth},
    }}
    sibling_before = deepcopy(state["account_pool_runtime"]["pool_b"])
    failed_worker = {
        "run_id": "old-worker-on-a", "logical_agent_id": "codex", "provider": "codex",
        "started_at": "2026-09-11T02:05:00Z", "auth_identity_hash": "auth-a",
    }
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value=current_auth),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor, "utc_now", return_value="2026-09-11T02:06:00Z"),
    ):
        supervisor.mark_account_pool_cooldown(
            config, state, failed_worker, "quota exhausted", failure_kind="quota_terminal",
            blocked_until=datetime(2026, 9, 11, 3, 0, tzinfo=UTC),
        )
    if current_auth == "auth-a":
        assert state["account_pool_runtime"]["pool_b"]["state"] == "cooldown"
    else:
        assert state["account_pool_runtime"]["pool_b"] == sibling_before


@pytest.mark.parametrize("failure_kind", ["auth", "quota_terminal"])
@pytest.mark.parametrize("stale_save_after_clear", [False, True])
def test_clear_does_not_restore_auth_cooldown_from_pre_failure_writer(tmp_path, failure_kind, stale_save_after_clear):
    config = {
        "paths": {
            "state_file": str(tmp_path / "state.json"),
            "event_queue": str(tmp_path / "events.jsonl"),
            "activity_log": str(tmp_path / "activity.jsonl"),
        },
        "account_pools": {"pool_a": {"max_concurrent": 3, "enabled": True, "state": "healthy"}},
        "agents": {"codex": {"id": "codex", "provider": "codex", "account_pool": "pool_a"}},
        "providers": {"codex": {"delivery_mode": "codex", "quota_group": "codex"}},
    }
    Path(config["paths"]["event_queue"]).write_text("")
    state = runtime_state.default_state()
    state["account_pool_runtime"]["pool_a"] = {
        "state": "healthy", "effective_concurrency": 3, "generation": 0,
        "auth_identity_hash": "fixture-auth",
    }
    runtime_state.save_runtime_state(config, state)
    old_healthy_writer = runtime_state.load_runtime_state(config)
    failure_writer = runtime_state.load_runtime_state(config)
    with (
        mock.patch.object(supervisor, "provider_auth_identity_hash", return_value="fixture-auth"),
        mock.patch.object(supervisor, "write_activity_log"),
        mock.patch.object(supervisor.model_rotation, "rotation_enabled", return_value=False),
    ):
        with mock.patch.object(supervisor, "utc_now", return_value="2099-09-11T02:00:00Z"):
            assert supervisor.mark_provider_dispatch_paused(
                config, failure_writer, "codex", "authentication failed" if failure_kind == "auth" else "usage limit reached",
                worker_run_id="failure-run", failure_kind=failure_kind,
                worker={"run_id": "failure-run", "logical_agent_id": "codex", "provider": "codex", "auth_identity_hash": "fixture-auth"},
            )
        runtime_state.save_runtime_state(config, failure_writer)
        assert runtime_state.load_runtime_state(config)["account_pool_runtime"]["pool_a"]["state"] == "cooldown"
        clearer = runtime_state.load_runtime_state(config)
        if not stale_save_after_clear:
            runtime_state.save_runtime_state(config, old_healthy_writer)
        with mock.patch.object(supervisor, "utc_now", return_value="2099-09-11T02:01:00Z"):
            assert supervisor.clear_provider_dispatch_pause(config, clearer, "codex")
        expected = "cooldown" if failure_kind == "auth" else "recovering"
        assert clearer["account_pool_runtime"]["pool_a"]["state"] == expected
        runtime_state.save_runtime_state(config, clearer)
        assert runtime_state.load_runtime_state(config)["account_pool_runtime"]["pool_a"]["state"] == expected
        if stale_save_after_clear:
            runtime_state.save_runtime_state(config, old_healthy_writer)
        restored = runtime_state.load_runtime_state(config)
        pool = restored["account_pool_runtime"]["pool_a"]
        capacity = supervisor.account_pool_effective_concurrency(config, restored, "codex")
        assert (pool["state"], capacity) == (expected, 0 if failure_kind == "auth" else 1), pool


class PoolFailureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2099, 9, 11, 1, 0, tzinfo=UTC)


@pytest.mark.parametrize('failure_kind', ['quota_terminal', 'auth'])
def test_rotated_auth_canary_survives_real_disk_save(tmp_path, failure_kind):
    config = {
        'paths': {
            'state_file': str(tmp_path / 'state.json'),
            'event_queue': str(tmp_path / 'events.jsonl'),
            'activity_log': str(tmp_path / 'activity.jsonl'),
        },
        'account_pools': {'pool_a': {'max_concurrent': 3, 'enabled': True, 'state': 'healthy'}},
        'agents': {'codex': {'id': 'codex', 'provider': 'codex', 'account_pool': 'pool_a'}},
        'providers': {'codex': {'delivery_mode': 'codex', 'quota_group': 'codex'}},
    }
    Path(config['paths']['event_queue']).write_text('')
    state = runtime_state.default_state()
    worker = {'run_id': 'old-auth-failure', 'logical_agent_id': 'codex', 'provider': 'codex', 'auth_identity_hash': 'auth-a'}
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-a'),
        mock.patch.object(supervisor, 'datetime', PoolFailureClock),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:00:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
        mock.patch.object(supervisor.model_rotation, 'rotation_enabled', return_value=False),
    ):
        assert supervisor.mark_provider_dispatch_paused(
            config, state, 'codex', 'usage limit reached' if failure_kind == 'quota_terminal' else 'authentication failed',
            worker_run_id=worker['run_id'], worker=worker, failure_kind=failure_kind,
        )
    runtime_state.save_runtime_state(config, state)
    rotated_writer = runtime_state.load_runtime_state(config)
    assert rotated_writer['account_pool_runtime']['pool_a']['state'] == 'cooldown'
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-b'),
        mock.patch.object(supervisor, 'write_activity_log'),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:01:00Z'),
    ):
        assert supervisor.expire_provider_dispatch_pauses(config, rotated_writer)
        lifecycle, entry = supervisor.account_pool_runtime_state(
            config, rotated_writer, 'codex', now=datetime(2099, 9, 11, 1, 1, tzinfo=UTC)
        )
    assert (lifecycle, entry['auth_identity_hash'], entry['effective_concurrency']) == ('recovering', 'auth-b', 1)
    runtime_state.save_runtime_state(config, rotated_writer)
    restored = runtime_state.load_runtime_state(config)
    pool = restored['account_pool_runtime']['pool_a']
    assert (pool['state'], pool['auth_identity_hash'], pool['effective_concurrency']) == ('recovering', 'auth-b', 1), pool

    # A stale writer for A cannot undo B's admitted recovery.
    runtime_state.save_runtime_state(config, state)
    restored = runtime_state.load_runtime_state(config)
    assert restored['account_pool_runtime']['pool_a']['auth_identity_hash'] == 'auth-b'
    assert restored['account_pool_runtime']['pool_a']['state'] == 'recovering'
    stale_b = deepcopy(restored)

    # Retain predecessor evidence across more than one credential rotation.
    with mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-c'):
        lifecycle, entry = supervisor.account_pool_runtime_state(
            config, restored, 'codex', now=datetime(2099, 9, 11, 1, 2, tzinfo=UTC)
        )
    assert lifecycle == 'recovering'
    runtime_state.save_runtime_state(config, restored)
    for stale in [state, stale_b]:
        runtime_state.save_runtime_state(config, stale)
        persisted = runtime_state.load_runtime_state(config)['account_pool_runtime']['pool_a']
        assert (persisted['state'], persisted['auth_identity_hash'], persisted['effective_concurrency']) == ('recovering', 'auth-c', 1)

    # The predecessor proof cannot erase a different, later failure on C.
    stale_recovery = runtime_state.load_runtime_state(config)
    later = deepcopy(stale_recovery)
    later_worker = {'run_id': 'later-auth-c-failure', 'logical_agent_id': 'codex', 'provider': 'codex', 'auth_identity_hash': 'auth-c'}
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-c'),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:03:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
        mock.patch.object(supervisor.model_rotation, 'rotation_enabled', return_value=False),
    ):
        supervisor.mark_account_pool_cooldown(
            config, later, later_worker, 'later authentication failure', failure_kind='auth',
            blocked_until=datetime(2099, 9, 11, 2, 0, tzinfo=UTC),
        )
    runtime_state.save_runtime_state(config, later)
    runtime_state.save_runtime_state(config, stale_recovery)
    persisted = runtime_state.load_runtime_state(config)['account_pool_runtime']['pool_a']
    assert persisted['state'] == 'cooldown'
    assert persisted['auth_identity_hash'] == 'auth-c'
    assert persisted['last_worker_run_id'] == 'later-auth-c-failure'


class PoolExpiryClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2099, 9, 11, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize('stale_save', [False, True])
@pytest.mark.parametrize('prior_epoch', [False, True])
def test_provider_expiry_does_not_allow_pre_failure_healthy_to_bypass_canary(tmp_path, stale_save, prior_epoch):
    config = {
        'paths': {
            'state_file': str(tmp_path / 'state.json'),
            'event_queue': str(tmp_path / 'events.jsonl'),
            'activity_log': str(tmp_path / 'activity.jsonl'),
        },
        'account_pools': {'pool_a': {'max_concurrent': 3, 'enabled': True, 'state': 'healthy'}},
        'agents': {'codex': {'id': 'codex', 'provider': 'codex', 'account_pool': 'pool_a'}},
        'providers': {'codex': {'delivery_mode': 'codex', 'quota_group': 'codex'}},
    }
    Path(config['paths']['event_queue']).write_text('')
    state = runtime_state.default_state()
    state['account_pool_runtime']['pool_a'] = {
        'state': 'healthy', 'generation': 0, 'effective_concurrency': 3, 'auth_identity_hash': 'auth-a'
    }
    if prior_epoch:
        state['account_pool_runtime']['pool_a'].update(
            last_failure_at='2099-09-11T01:00:00Z',
            last_worker_run_id='earlier-same-second-failure',
            last_recovered_at='2099-09-11T01:00:00Z',
        )
    runtime_state.save_runtime_state(config, state)
    pre_failure_writer = runtime_state.load_runtime_state(config)
    worker = {'run_id': 'failure-run', 'logical_agent_id': 'codex', 'provider': 'codex', 'auth_identity_hash': 'auth-a'}
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-a'),
        mock.patch.object(supervisor, 'datetime', PoolFailureClock),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:00:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
        mock.patch.object(supervisor.model_rotation, 'rotation_enabled', return_value=False),
    ):
        assert supervisor.mark_provider_dispatch_paused(
            config, state, 'codex', 'usage limit reached',
            worker_run_id=worker['run_id'], worker=worker, failure_kind='quota_terminal',
        )
    runtime_state.save_runtime_state(config, state)
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-a'),
        mock.patch.object(supervisor, 'datetime', PoolExpiryClock),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T03:00:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
    ):
        assert supervisor.expire_provider_dispatch_pauses(config, state)
    runtime_state.save_runtime_state(config, state)
    assert state['account_pool_runtime']['pool_a']['state'] == 'cooldown'
    if stale_save:
        runtime_state.save_runtime_state(config, pre_failure_writer)
    restored = runtime_state.load_runtime_state(config)
    assert not restored['provider_guardrails']['dispatch_pauses']
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='auth-a'),
        mock.patch.object(supervisor, 'datetime', PoolExpiryClock),
    ):
        capacity = supervisor.account_pool_effective_concurrency(config, restored, 'codex')
    assert capacity <= 1, restored['account_pool_runtime']['pool_a']
