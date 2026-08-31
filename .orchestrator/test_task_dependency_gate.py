"""Focused regression tests for canonical dependency updates and launch gates."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Focused tests must not depend on the worker's live coordination environment.
# In particular, a clean task worktree has no gitignored config.json, while
# actor validation in the canonical CLI still needs the declared roster. Keep
# this override local to each test: module-level environment writes can leak
# into scripts/test_ai_status.py during pytest collection.
_TEST_CONFIG = THIS_DIR / "config.example.json"

import ai_status
import supervisor
from task_archive import TaskResolver


def canonical_test_environment():
    return mock.patch.dict(
        os.environ,
        {
            "ORCH_CONFIG_PATH": str(_TEST_CONFIG),
            "PANTHEON_CONFIG_PATH": str(_TEST_CONFIG),
        },
        clear=False,
    )


class DependencyGraphValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {
            "id": "GATE-TARGET-001",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }
        self.upstream = {
            "id": "GATE-UPSTREAM-001",
            "status": "in_progress",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
        }

    def test_update_is_audited_after_graph_validation(self) -> None:
        state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        with (
            canonical_test_environment(),
            tempfile.TemporaryDirectory(prefix="pantheon-dependency-audit-") as tmpdir,
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
            mock.patch.object(ai_status, "LOG_FILE", Path(tmpdir) / "activity.jsonl"),
        ):
            ai_status.command_set_dependencies(
                state,
                ["GATE-TARGET-001", "GATE-UPSTREAM-001", "等待上游完成"],
            )
            audit = json.loads((Path(tmpdir) / "activity.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(state["tasks"][0]["depends_on"], ["GATE-UPSTREAM-001"])
        self.assertEqual(audit["type"], "dependency_update")
        self.assertEqual(audit["old_dependencies"], [])
        self.assertEqual(audit["new_dependencies"], ["GATE-UPSTREAM-001"])
        self.assertEqual(audit["source"], "canonical_cli")

    def test_dependency_mutation_exposes_only_the_canonical_command(self) -> None:
        self.assertIs(ai_status.MUTATING_COMMANDS["set_dependencies"], ai_status.command_set_dependencies)
        for alias in (
            "set-dependencies",
            "set_dependency",
            "set-dependency",
            "update_dependencies",
            "update-dependencies",
            "dependency",
        ):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, ai_status.MUTATING_COMMANDS)

    def test_invalid_dependency_update_does_not_mutate_state(self) -> None:
        state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        before = deepcopy(state)
        for dependencies in (
            ["GATE-TARGET-001"],
            ["GATE-MISSING-001"],
            ["GATE-UPSTREAM-001", "GATE-UPSTREAM-001"],
        ):
            with (
                self.subTest(dependencies=dependencies),
                canonical_test_environment(),
                mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
                mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
            ):
                with self.assertRaises(SystemExit):
                    ai_status.command_set_dependencies(
                        state,
                        ["GATE-TARGET-001", ",".join(dependencies), "invalid graph"],
                    )
            self.assertEqual(state, before)

    def test_reachable_cycle_and_live_archive_duplicate_are_rejected(self) -> None:
        cycle_state = {
            "tasks": [
                {**deepcopy(self.target), "depends_on": []},
                {**deepcopy(self.upstream), "id": "GATE-UPSTREAM-001", "depends_on": ["GATE-TARGET-001"]},
            ]
        }
        with (
            canonical_test_environment(),
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            with self.assertRaisesRegex(SystemExit, "cycle"):
                ai_status.command_set_dependencies(
                    cycle_state,
                    ["GATE-TARGET-001", "GATE-UPSTREAM-001", "cycle test"],
                )

        duplicate_state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        with (
            canonical_test_environment(),
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(
                ai_status,
                "load_archived_snapshot",
                return_value={"task_id": "GATE-UPSTREAM-001", "terminal_status": "done"},
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "BOTH"):
                ai_status.command_set_dependencies(
                    duplicate_state,
                    ["GATE-TARGET-001", "GATE-UPSTREAM-001", "duplicate lifecycle"],
                )


class DependencyIdentityConsistencyTests(unittest.TestCase):
    """The validator's verdict must match what the runtime can actually resolve.

    Board lookup (``task_index_from_status``) and archive lookup
    (``archive_task_path``) are both case-sensitive, so a dependency that
    differs from a real task id by case alone resolves nowhere.  If the graph
    validator folded case it would call that edge satisfied-in-principle, let
    the edit land, and leave the task waiting on it forever while reporting a
    clean graph.
    """

    def setUp(self) -> None:
        self.target = {
            "id": "GATE-CASE-TARGET-001",
            "status": "todo",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }
        self.upstream = {
            "id": "GATE-CASE-UPSTREAM-001",
            "status": "done",
            "owner": "Claude",
            "reviewer": "Codex",
            "depends_on": [],
        }

    def test_case_mismatched_dependency_is_rejected_by_the_canonical_cli(self) -> None:
        state = {"tasks": [deepcopy(self.target), deepcopy(self.upstream)]}
        before = deepcopy(state)
        with (
            canonical_test_environment(),
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
        ):
            with self.assertRaisesRegex(SystemExit, "case-sensitive"):
                ai_status.command_set_dependencies(
                    state,
                    [self.target["id"], self.upstream["id"].lower(), "case mismatch"],
                )
        self.assertEqual(state, before)

    def test_clean_graph_verdict_implies_the_resolver_can_resolve_every_edge(self) -> None:
        for spelling in (
            self.upstream["id"],
            self.upstream["id"].lower(),
            self.upstream["id"].title(),
            # Surrounding whitespace is the one difference the resolver does
            # fold away, so this spelling must stay clean on both sides.
            "  " + self.upstream["id"] + "  ",
        ):
            with self.subTest(spelling=spelling):
                candidate = {**deepcopy(self.target), "depends_on": [spelling]}
                board = [candidate, deepcopy(self.upstream)]
                with mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
                    errors = ai_status.dependency_graph_errors_for_task(candidate, board)

                resolver = TaskResolver({item["id"]: item for item in board})
                resolvable = resolver.get(spelling) is not None
                # The invariant under test: a clean verdict is only permitted
                # when every declared edge actually resolves.
                self.assertEqual(
                    not errors,
                    resolvable,
                    f"validator verdict disagrees with resolver for {spelling!r}: {errors}",
                )

    def test_case_variant_of_the_task_itself_is_reported_as_a_self_edge(self) -> None:
        candidate = {**deepcopy(self.target), "depends_on": [self.target["id"].lower()]}
        with mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
            errors = ai_status.dependency_graph_errors_for_task(
                candidate, [candidate, deepcopy(self.upstream)]
            )
        self.assertTrue(any("depends on itself" in error for error in errors), errors)

    def test_case_variant_alongside_the_real_edge_is_still_rejected(self) -> None:
        # Folding these two spellings into one "duplicate" would hide the fact
        # that the second edge can never resolve.
        candidate = {
            **deepcopy(self.target),
            "depends_on": [self.upstream["id"], self.upstream["id"].lower()],
        }
        with mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
            errors = ai_status.dependency_graph_errors_for_task(
                candidate, [candidate, deepcopy(self.upstream)]
            )
        self.assertTrue(any("case-sensitive" in error for error in errors), errors)

    def test_case_mismatched_dependency_never_reaches_the_dispatch_gate_as_ready(self) -> None:
        task = {
            "id": "GATE-CASE-DISPATCH-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["gate-case-upstream-001"],
        }
        upstream = deepcopy(self.upstream)
        task_map = {task["id"]: task, upstream["id"]: upstream}
        with mock.patch.object(ai_status, "load_archived_snapshot", return_value=None):
            self.assertFalse(supervisor.dependencies_satisfied(task, task_map, {"done"}))


class DependencyDispatchGateTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "schema": {
                "tasks_path": "tasks",
                "task_id_field": "id",
                "assignee_field": "owner",
                "reviewer_field": "reviewer",
            },
            "ready_dispatcher": {"dependency_done_statuses": ["done"]},
            "agents": {
                "codex": {"id": "codex", "display_name": "Codex", "provider": "codex"},
            },
            "providers": {"codex": {}},
        }

    def test_stale_in_progress_special_case_still_obeys_dependency_gate(self) -> None:
        task = {
            "id": "GATE-STALE-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
        }
        upstream = {"id": "GATE-UPSTREAM-001", "status": "in_progress", "depends_on": []}
        event = {
            "event_key": "old-dispatch-key",
            "task_id": task["id"],
            "target_agent": "codex",
            "target_display_name": "Codex",
            "reason": "owned_ready_dispatch",
        }

        message = supervisor.stale_dispatch_skip_message(
            self._config(), event, {task["id"]: task, upstream["id"]: upstream}
        )

        self.assertIsNotNone(message)
        self.assertIn("dependency gate", message or "")

    def test_finalize_survives_dependency_update_without_duplicate_or_skip(self) -> None:
        task = {
            "id": "GATE-FINALIZE-001",
            "status": "review_approved",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": [],
        }
        upstream = {
            "id": "GATE-FINALIZE-UPSTREAM-001",
            "status": "in_progress",
            "depends_on": [],
        }
        config = self._config()
        initial_map = {task["id"]: task, upstream["id"]: upstream}
        with mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None):
            before = supervisor.build_dispatch_event(
                task,
                "Codex",
                "owned_finalize_dispatch",
                initial_map,
            )

        state = {"tasks": [deepcopy(task), deepcopy(upstream)]}
        # This is the only test here whose update is accepted, so it is the only
        # one that reaches append_log. Point LOG_FILE at a directory this test
        # owns: the module global is whatever the last test to touch it left
        # behind, which under the full suite is a deleted temporary directory.
        with (
            canonical_test_environment(),
            tempfile.TemporaryDirectory(prefix="pantheon-dependency-finalize-") as tmpdir,
            mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
            mock.patch.object(ai_status, "load_archived_snapshot", return_value=None),
            mock.patch.object(ai_status, "LOG_FILE", Path(tmpdir) / "activity.jsonl"),
        ):
            ai_status.command_set_dependencies(
                state,
                [task["id"], upstream["id"], "保留已合併 PR 的 finalize closeout，不將 dependency 當成執行 gate"],
            )
            audit = json.loads((Path(tmpdir) / "activity.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(audit["type"], "dependency_update")
        self.assertEqual(audit["new_dependencies"], [upstream["id"]])

        updated_task = state["tasks"][0]
        updated_map = {updated_task["id"]: updated_task, upstream["id"]: upstream}
        with mock.patch.object(supervisor, "resolve_task_progress_head", return_value=None):
            after = supervisor.build_dispatch_event(
                updated_task,
                "Codex",
                "owned_finalize_dispatch",
                updated_map,
            )
            stale_message = supervisor.stale_dispatch_skip_message(
                config,
                {
                    "event_key": before["key"],
                    "task_id": updated_task["id"],
                    "target_agent": "codex",
                    "target_display_name": "Codex",
                    "reason": "owned_finalize_dispatch",
                },
                updated_map,
            )

        self.assertEqual(before["key"], after["key"])
        self.assertIsNone(stale_message)

    def test_blocked_recovery_requires_a_task_snapshot_when_dependencies_exist(self) -> None:
        task = {
            "id": "GATE-BLOCKED-001",
            "status": "blocked",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
            "next": "stale dispatch provider failure; retry",
        }

        self.assertFalse(
            supervisor.blocked_task_auto_recovery_eligible(self._config(), task, None)
        )

    def test_queue_revalidates_canonical_dependencies_before_start(self) -> None:
        task = {
            "id": "GATE-TOCTOU-001",
            "status": "in_progress",
            "owner": "Codex",
            "reviewer": "Claude",
            "depends_on": ["GATE-UPSTREAM-001"],
            "last_update": "2026-08-31T00:00:00Z",
        }
        ready_upstream = {"id": "GATE-UPSTREAM-001", "status": "done", "depends_on": []}
        blocked_upstream = {"id": "GATE-UPSTREAM-001", "status": "blocked", "depends_on": []}
        config = self._config()
        event = supervisor.build_dispatch_event(
            task,
            "Codex",
            "owned_in_progress_dispatch",
            {task["id"]: task, ready_upstream["id"]: ready_upstream},
        )
        event.update(
            {
                "event_id": "evt-gate-toctou",
                "event_key": event["key"],
                "target_agent": "codex",
                "target_display_name": "Codex",
            }
        )
        initial_status = {"tasks": [task, ready_upstream]}
        latest_status = {"tasks": [task, blocked_upstream]}
        state = {"queue": {"events": {}}, "workers": {}}

        with (
            mock.patch.object(supervisor, "load_event_queue", return_value=[event]),
            mock.patch.object(supervisor, "load_status", side_effect=[initial_status, latest_status]),
            mock.patch.object(supervisor, "build_request", return_value=SimpleNamespace(
                provider="codex", agent_id="codex", task_id=task["id"], metadata={}
            )),
            mock.patch.object(supervisor, "prepare_worker_workspace", return_value=(True, None)),
            mock.patch.object(supervisor, "start_worker_for_request", side_effect=AssertionError("gate must stop launch")),
            mock.patch.object(supervisor, "write_activity_log"),
        ):
            changed = supervisor.process_queue(config, state, provider_report={})

        self.assertTrue(changed)
        self.assertEqual(state["queue"]["events"][event["event_id"]]["skip_reason"], "stale_dispatch_event")


if __name__ == "__main__":
    unittest.main()
