#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import runtime_state


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

    def test_load_runtime_state_adds_chair_rotation_defaults(self) -> None:
        self._write_json(self.root / "state.json", {"workers": {}, "queue": {"events": {}}})
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")

        state = runtime_state.load_runtime_state(self.config)

        self.assertEqual(state["chair_rotation"]["current_index"], 0)
        self.assertIsNone(state["chair_rotation"]["last_chair_agent"])
        self.assertIn("chair_review", state["supervisor"]["mode_occupancy"])

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

    def test_ready_dispatch_weighted_cursor_survives_save_and_reload(self) -> None:
        (self.root / "event-queue.jsonl").write_text("", encoding="utf-8")
        state = runtime_state.default_state()
        state["ready_dispatcher"]["weighted_cursor"] = 73
        state["ready_dispatcher"]["weighted_cursor_revision"] = 19
        state["ready_dispatcher"]["weighted_cursor_updated_at"] = "2026-07-31T12:00:00Z"

        runtime_state.save_runtime_state(self.config, state)
        reloaded = runtime_state.load_runtime_state(self.config)

        self.assertEqual(reloaded["ready_dispatcher"]["weighted_cursor"], 73)
        self.assertEqual(reloaded["ready_dispatcher"]["weighted_cursor_revision"], 19)
        self.assertEqual(
            reloaded["ready_dispatcher"]["weighted_cursor_updated_at"],
            "2026-07-31T12:00:00Z",
        )

    def test_ready_dispatch_weighted_cursor_migration_fails_safe(self) -> None:
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
                self.assertEqual(migrated["ready_dispatcher"]["weighted_cursor"], 0)

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
                    migrated["ready_dispatcher"]["weighted_cursor_revision"],
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
            migrated["ready_dispatcher"]["weighted_cursor_updated_at"]
        )

    def test_newer_cursor_revision_wins_with_equal_wall_clock_timestamp(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "weighted_cursor": 17,
            "weighted_cursor_revision": 41,
            "weighted_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        newer_state = runtime_state.default_state()
        newer_state["ready_dispatcher"] = {
            "weighted_cursor": 18,
            "weighted_cursor_revision": 42,
            "weighted_cursor_updated_at": "2026-07-31T12:00:00Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, newer_state)

        self.assertEqual(merged["ready_dispatcher"]["weighted_cursor"], 18)
        self.assertEqual(
            merged["ready_dispatcher"]["weighted_cursor_revision"],
            42,
        )

    def test_concurrent_auxiliary_save_cannot_roll_back_weighted_cursor(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "weighted_cursor": 17,
            "weighted_cursor_revision": 42,
            "weighted_cursor_updated_at": "2026-07-31T11:59:00Z",
        }
        stale_claim_state = runtime_state.default_state()
        stale_claim_state["ready_dispatcher"] = {
            "weighted_cursor": 3,
            "weighted_cursor_revision": 41,
            "weighted_cursor_updated_at": "2099-12-31T23:59:59Z",
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

        self.assertEqual(merged["ready_dispatcher"]["weighted_cursor"], 17)
        self.assertEqual(
            merged["ready_dispatcher"]["weighted_cursor_revision"],
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
            "weighted_cursor": 17,
            "weighted_cursor_revision": 42,
            "weighted_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        runtime_state.save_runtime_state(self.config, disk_state)

        stale_claim_state = runtime_state.default_state()
        stale_claim_state["ready_dispatcher"] = {
            "weighted_cursor": 3,
            "weighted_cursor_revision": 41,
            "weighted_cursor_updated_at": "2099-12-31T23:59:59Z",
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

        self.assertEqual(reloaded["ready_dispatcher"]["weighted_cursor"], 17)
        self.assertEqual(
            reloaded["ready_dispatcher"]["weighted_cursor_revision"],
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

    def test_equal_cursor_revision_prefers_durable_disk_snapshot(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "weighted_cursor": 17,
            "weighted_cursor_revision": 42,
            "weighted_cursor_updated_at": "2026-07-31T12:00:00Z",
        }
        auxiliary_state = runtime_state.default_state()
        auxiliary_state["ready_dispatcher"] = {
            "weighted_cursor": 3,
            "weighted_cursor_revision": 42,
            "weighted_cursor_updated_at": "2099-12-31T23:59:59Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, auxiliary_state)

        self.assertEqual(merged["ready_dispatcher"]["weighted_cursor"], 17)
        self.assertEqual(
            merged["ready_dispatcher"]["weighted_cursor_revision"],
            42,
        )

    def test_malformed_revision_and_future_timestamp_cannot_pin_cursor(self) -> None:
        disk_state = runtime_state.default_state()
        disk_state["ready_dispatcher"] = {
            "weighted_cursor": 17,
            "weighted_cursor_revision": "not-a-revision",
            "weighted_cursor_updated_at": "2099-12-31T23:59:59Z",
        }
        valid_state = runtime_state.default_state()
        valid_state["ready_dispatcher"] = {
            "weighted_cursor": 18,
            "weighted_cursor_revision": 1,
            "weighted_cursor_updated_at": "2026-07-31T12:00:00Z",
        }

        merged = runtime_state.merge_runtime_states(disk_state, valid_state)

        self.assertEqual(merged["ready_dispatcher"]["weighted_cursor"], 18)
        self.assertEqual(
            merged["ready_dispatcher"]["weighted_cursor_revision"],
            1,
        )
