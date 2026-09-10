from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_engine
import supervisor


class PausedOwnerFailoverPreferenceTests(unittest.TestCase):
    """The paused-owner failover picks the first candidate that passes, so its
    ordering *is* its owner preference.

    That ordering came from `agent_dispatch_preference_rank`, which reads the
    provider *name*: anything not spelled antigravity/claude/codex sorts last,
    however it is actually delivered. This path therefore had a second, weaker,
    unconfigurable opinion about which lane should implement -- and it could
    hand a task to a lane with no free slot, because reserving is checked per
    dispatch slot while candidates are logical agents. Both now go through the
    same policy the reassignment selector uses.
    """

    PREFERENCE = {
        "enabled": True,
        "preferred_providers": ["antigravity"],
        "task_classes": ["implementation", "remediation", "documentation"],
    }

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "event_queue.jsonl").write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _config(self, preference: dict[str, Any] | None = "default") -> dict[str, Any]:
        config: dict[str, Any] = {
            "paths": {
                "event_queue": str(self.root / "event_queue.jsonl"),
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "activity.jsonl"),
            },
            "ready_dispatcher": {
                "review_statuses": ["review"],
                "finalize_statuses": ["review_approved"],
                "owned_statuses": ["in_progress", "todo"],
                "active_worker_statuses": ["running"],
            },
            "providers": {
                "claude": {"delivery_mode": "claude_cli"},
                "codex": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
                "codex_lupin": {
                    "delivery_mode": "codex",
                    "codex": {"codex_home": str(self.root / "codex-home")},
                },
                # An agy lane whose provider key is not spelled "antigravity".
                # Only the configured delivery mode says what runs the work.
                "agy_pool_b": {"delivery_mode": "antigravity"},
            },
            "agents": {
                "claude": {
                    "display_name": "Claude",
                    "provider": "claude",
                    "adapter": "claude_cli",
                    "account_pool": "claude_main",
                },
                "codex": {
                    "display_name": "Codex",
                    "provider": "codex",
                    "adapter": "codex",
                    "account_pool": "codex_bjoe",
                },
                "codex2": {
                    "display_name": "Codex2",
                    "provider": "codex_lupin",
                    "adapter": "codex",
                    "account_pool": "codex_lupin",
                },
                "agy_b": {
                    "display_name": "AgyB",
                    "provider": "agy_pool_b",
                    "adapter": "antigravity",
                    "account_pool": "agy_b_pool",
                },
                "agy_b_slot_1": {
                    "display_name": "agy_b_slot_1",
                    "provider": "agy_pool_b",
                    "adapter": "antigravity",
                    "account_pool": "agy_b_pool",
                    "dispatch_slot_for_pool": "agy_b_pool",
                },
            },
            "account_pools": {
                "claude_main": {"enabled": True, "max_concurrent": 1},
                "codex_bjoe": {"enabled": True, "max_concurrent": 1},
                "codex_lupin": {"enabled": True, "max_concurrent": 1},
                "agy_b_pool": {"enabled": True, "max_concurrent": 1},
            },
        }
        if preference == "default":
            preference = dict(self.PREFERENCE)
        if preference is not None:
            config["ready_dispatcher"]["owner_provider_preference"] = preference
        return config

    @staticmethod
    def _paused_codex_state() -> dict[str, Any]:
        return {
            "workers": {},
            "queue": {"events": {}},
            "provider_guardrails": {
                "dispatch_pauses": {
                    "codex": {
                        "provider": "codex",
                        "blocked_until": "2099-01-01T00:00:00Z",
                        "reason": "Codex usage limit reached",
                    }
                }
            },
        }

    @staticmethod
    def _owned_task(**overrides: Any) -> dict[str, Any]:
        """Owned implementation work whose owner is still a live choice.

        This used to be `review_approved`, which is the one owner state the
        preference must never touch: that status pins an exact reviewed head
        and closeout from it is read-only, so "which lane should implement
        this" is already answered. The preference is about work that is still
        to be written, so the fixture states work that is still to be written.
        """
        task = {
            "id": "T-1",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "task_class": "implementation",
        }
        task.update(overrides)
        return {"tasks": [task]}

    def _run(self, config: dict[str, Any], state: dict[str, Any], status: dict[str, Any]) -> dict[str, Any] | None:
        with (
            mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "console_log"),
        ):
            dispatch_engine.reassign_unavailable_reviewers(
                config, copy.deepcopy(state), copy.deepcopy(status)
            )
        return persist.call_args.kwargs if persist.call_args else None

    def test_candidate_order_without_the_preference_puts_codex_first(self) -> None:
        """Baseline: the dispatch loop ranks the agy lane last by provider name."""
        self.assertEqual(
            supervisor.dispatch_loop_agent_ids(self._config()),
            ["claude", "codex", "codex2", "agy_b"],
        )
        reassignment = self._run(
            self._config(preference=None), self._paused_codex_state(), self._owned_task()
        )
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_paused_owner_fails_over_to_the_preferred_provider(self) -> None:
        reassignment = self._run(self._config(), self._paused_codex_state(), self._owned_task())
        self.assertIsNotNone(reassignment)
        self.assertEqual(reassignment["new_owner"], "AgyB")
        self.assertEqual(reassignment["new_reviewer"], "Claude")
        self.assertEqual(reassignment["handoff_from"], "Codex")

    def test_saturated_preferred_lane_falls_back_to_the_existing_order(self) -> None:
        """Its one slot is running, so preferring it would only queue the task."""
        state = self._paused_codex_state()
        state["workers"]["run-1"] = {
            "status": "running",
            "agent_id": "agy_b_slot_1",
            "logical_agent_id": "agy_b",
            "quota_group": "agy_b_pool",
            "task_id": "T-OTHER",
            "request_snapshot": {"reason": "owned_ready_dispatch"},
        }
        reassignment = self._run(self._config(), state, self._owned_task())
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_pending_delivery_on_the_shared_pool_also_saturates_the_lane(self) -> None:
        """A queued-but-undelivered event holds the pool's only slot too.

        Nothing is running, so the preference's old per-agent load reads zero
        and the active-only quota guard lets the lane through -- yet the one
        slot this account owns is already spoken for.
        """
        queue_path = self.root / "event_queue.jsonl"
        queue_path.write_text(
            json.dumps(
                {
                    "event_id": "evt-1",
                    "target_agent": "AgyB",
                    "target_display_name": "AgyB",
                    "provider": "agy_pool_b",
                    "reason": "owned_ready_dispatch",
                    "message": "queued but not delivered",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        reassignment = self._run(self._config(), self._paused_codex_state(), self._owned_task())
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_frozen_closeout_states_keep_the_existing_candidate_order(self) -> None:
        """The preferred lane is free and the class is eligible; it still loses.

        A `review_approved` head, a recorded `approved_head`, or a task already
        on a merge route is owned by whoever wrote the reviewed commit. Failing
        an unavailable owner over is a control-plane repair either way, but it
        must land wherever the pre-preference order put it, not on the lane the
        implementation policy would rather use.
        """
        for label, overrides in (
            ("review_approved", {"status": "review_approved"}),
            ("approved_head", {"approved_head": "a" * 40}),
            ("merge_route", {"merge_route": {"route": "queued", "head": "a" * 40}}),
        ):
            with self.subTest(label=label):
                status = self._owned_task(id=f"T-{label}", **overrides)
                with_preference = self._run(self._config(), self._paused_codex_state(), status)
                without_preference = self._run(
                    self._config(preference=None), self._paused_codex_state(), status
                )
                self.assertEqual(
                    (with_preference or {}).get("new_owner"),
                    (without_preference or {}).get("new_owner"),
                )
                self.assertEqual((with_preference or {}).get("new_owner"), "Codex2")

    def test_deployment_task_classes_keep_the_existing_order(self) -> None:
        reassignment = self._run(
            self._config(),
            self._paused_codex_state(),
            self._owned_task(id="T-3", task_class="runtime_release"),
        )
        self.assertEqual((reassignment or {}).get("new_owner"), "Codex2")

    def test_reviewer_failover_order_is_unchanged(self) -> None:
        """The reviewer branch is not an implementation-owner decision."""
        status = {
            "tasks": [
                {
                    "id": "T-2",
                    "status": "review",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "task_class": "implementation",
                }
            ]
        }
        reassignment = self._run(self._config(), self._paused_codex_state(), status)
        self.assertIsNotNone(reassignment)
        self.assertEqual(reassignment["new_reviewer"], "Codex2")
        self.assertEqual(reassignment["new_owner"], "Claude")


if __name__ == "__main__":
    unittest.main()
