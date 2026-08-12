#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
